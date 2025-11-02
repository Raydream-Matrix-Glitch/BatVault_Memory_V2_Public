import asyncio, functools, io, os, time, inspect
from typing import List, Optional, Any, Mapping
from typing import Iterator
from core_utils import jsonx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from core_http.client import get_http_client
from core_utils.sse import stream_answer_with_final, stream_chunks
from core_utils.fingerprints import schema_dir_fp
from core_http.headers import (
    REQUEST_SNAPSHOT_ETAG, BV_POLICY_FP, BV_ALLOWED_IDS_FP,
    extract_policy_headers, BV_GRAPH_FP, RESPONSE_SNAPSHOT_ETAG,
)
from .evidence import EvidenceBuilder
from core_storage.artifact_index import (
     build_named_bundles, upload_named_bundles)
from pathlib import Path
import core_models
from .validator import view_artifacts_order
from pydantic import BaseModel, Field, ConfigDict, model_validator
from core_utils.ids import compute_request_id, generate_request_id
from core_config import get_settings
from core_logging import get_logger, trace_span, log_stage, bind_request_id
from core_utils.fastapi_bootstrap import setup_service
try:
    from core_observability.otel import inject_trace_context  # noqa: F401
except ImportError:  # pragma: no cover
    def inject_trace_context(hdrs=None):
        # Minimal hardening when core_observability is unavailable: strip context headers
        _in = dict(hdrs or {})
        return {k: v for k, v in _in.items() if k.lower() not in ("x-trace-id", "traceparent", "tracestate")}
from core_models_gen import (
    WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse
)
from core_utils.health import attach_health_routes
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from core_utils.load_shed import should_load_shed, start_background_refresh, stop_background_refresh
from .builder import build_why_decision_response
from .budget_gate import run_gate as budget_run_gate
from core_cache.redis_client import get_redis_pool
from core_cache import keys as cache_keys
from core_config.constants import TTL_BUNDLE_CACHE_SEC
from core_http.errors import attach_standard_error_handlers, raise_http_error
from core_metrics import counter as metric_counter
from core_logging.error_codes import ErrorCode
try:
    from minio.error import S3Error  # type: ignore
except ImportError:  # pragma: no cover - tests may not install minio
    class S3Error(Exception):
        pass
from core_idem import (
    idem_redis_key, idem_key_fp,
    idem_get, idem_set, idem_merge,
    idem_log_replay, idem_log_pending,
    idem_log_resume_seed, idem_log_progress, idem_log_complete,
    compute_request_scope_fp,
)

# ---- Configuration & globals ----------------------------------------------
settings        = get_settings()
logger          = get_logger("gateway")

# Resolve once per-process for headers; keep off hot path.
_SCHEMA_FP: str | None = None
def _schema_fp() -> str | None:
    global _SCHEMA_FP
    if _SCHEMA_FP is None:
        try:
            _SCHEMA_FP = schema_dir_fp(Path(core_models.__file__).parent / "schemas")
        except (FileNotFoundError, OSError, ValueError):
            _SCHEMA_FP = None
    return _SCHEMA_FP

# ---- Policy header extraction ---------------------------------------------
def _extract_policy_headers(request: Request) -> dict:
    """Centralised policy header selection."""
    return extract_policy_headers(request.headers)

_LOG_NO_ACTIVE_SPAN = os.getenv('GATEWAY_DEBUG_NO_ACTIVE_SPAN') == '1'

# Files included in the minimal "bundle_view" (schema-driven; Stage 8 taxonomy).
# Pull the canonical list directly from the schema (no heuristics).
VIEW_BUNDLE_FILES = list(view_artifacts_order())
# FE should not guess these – expose them via /config
BUNDLE_ARCHIVE_NAMES: tuple[str, ...] = ("bundle_view", "bundle_full")
# also surface the concrete view-artifact filenames so the audit drawer can render them
VIEW_ARTIFACT_NAMES: tuple[str, ...] = tuple(VIEW_BUNDLE_FILES)

# ---- Application & router --------------------------------------------------
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v3")

# Public config surface for frontend bootstrap
from core_config.settings import get_settings as _get_settings
from core_config.constants import (
    TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS, TIMEOUT_ENRICH_MS,
    TIMEOUT_VALIDATE_MS
)

@router.post("/ui/logs", include_in_schema=False)
async def ui_logs(request: Request):
    """Lightweight UI breadcrumb sink for FE correlation (dev/prod)."""
    payload = {}
    try:
        payload = await request.json()
    except (ValueError, TypeError, RuntimeError):
        payload = {}
    rid = request.headers.get("x-request-id") or "unknown"
    log_stage(logger, "ui", "event", request_id=rid, payload=payload)
    return {"ok": True}

# Allow GET/HEAD no-ops so accidental fetches don't spam 405s in consoles
@router.get("/ui/logs", include_in_schema=False)
@router.head("/ui/logs", include_in_schema=False)
async def ui_logs_noop():
    return Response(status_code=204)

def _resolve_signing_pubkey_b64() -> str:
    """
    Resolve the gateway’s public verifier key from ENV ONLY.
    Shared by /config and /keys/* so FE and ops always see the same value.
    """
    pub_b64 = os.getenv("GATEWAY_ED25519_PUB_B64", "").strip()
    if pub_b64:
        return pub_b64
    return ""

@app.get("/config")
def get_public_config(request: Request) -> dict:
    s = _get_settings()
    base = str(request.base_url).rstrip("/")
    pub_b64 = _resolve_signing_pubkey_b64()
    if not pub_b64:
        # keep this visible in logs so "verification skipped" in FE is explainable
        log_stage(
            logger, "config", "signing.pubkey_missing",
            request_id="startup",
            hint="set GATEWAY_ED25519_PUB_B64 or disable GATEWAY_SIGNING_REQUIRED",
        )
        # optional hard-fail to match deck: gateway refuses to serve /config without a key
        if os.getenv("GATEWAY_SIGNING_REQUIRED", "0").lower() in ("1", "true", "yes"):
            return JSONResponse(
                status_code=503,
                content={
                    "error": "signing_not_configured",
                    "detail": "Gateway is configured to require an Ed25519 public key but none was found."
                },
            )
    return {
        "gateway_base": base,
        "memory_base": f"{base}/memory",
        "endpoints": {
            "query": "/v3/query",
            "bundles": "/v3/bundles"
        },
        # FE consumes these to build correct /download URLs and to know what's inside a view bundle
        "bundle_archives": list(BUNDLE_ARCHIVE_NAMES),
        "bundle_view_files": list(VIEW_ARTIFACT_NAMES),
        "timeouts_ms": {
            "search": TIMEOUT_SEARCH_MS,
            "expand": TIMEOUT_EXPAND_MS,
            "enrich": TIMEOUT_ENRICH_MS,
            "validate": TIMEOUT_VALIDATE_MS
        },
        "signing": {
            "alg": "Ed25519",
            "public_key_b64": pub_b64
        }
    }

@app.get("/keys/gateway_ed25519_pub.b64")
def get_signing_pubkey_b64(request: Request) -> Response:
    """
    FE-first endpoint: serve the current verifier key exactly at the path the UI expects.
    This keeps FE → Gateway setups symmetrical with FE → Edge → Gateway.
    """
    pub_b64 = _resolve_signing_pubkey_b64()
    rid = request.headers.get("x-request-id") or "ui"
    if not pub_b64:
        log_stage(
            logger, "config", "signing.pubkey_fetch_miss",
            request_id=rid, path="/keys/gateway_ed25519_pub.b64",
        )
        # let the FE fall back to /config or to its public/ folder
        return Response(status_code=404, content="")
    log_stage(
        logger, "config", "signing.pubkey_fetch_hit",
        request_id=rid, path="/keys/gateway_ed25519_pub.b64",
    )
    return Response(content=pub_b64, media_type="text/plain")


@app.get("/keys/gateway_ed25519_pub.pem")
def get_signing_pubkey_pem(request: Request) -> Response:
    """
    Optional PEM view – keeps parity with the FE fallback sequence.
    """
    pub_b64 = _resolve_signing_pubkey_b64()
    rid = request.headers.get("x-request-id") or "ui"
    if not pub_b64:
        log_stage(
            logger, "config", "signing.pubkey_pem_miss",
            request_id=rid, path="/keys/gateway_ed25519_pub.pem",
        )
        return Response(status_code=404, content="")
    import base64, textwrap as _tw
    raw = base64.b64decode(pub_b64)
    b64_body = base64.b64encode(raw).decode("ascii")
    pem = "-----BEGIN PUBLIC KEY-----\\n" + "\\n".join(_tw.wrap(b64_body, 64)) + "\\n-----END PUBLIC KEY-----\\n"
    log_stage(
        logger, "config", "signing.pubkey_pem_hit",
        request_id=rid, path="/keys/gateway_ed25519_pub.pem",
    )
    return Response(content=pem, media_type="text/plain")

# Standardized wiring (observability, health, CORS, rate-limit via env)
setup_service(app, 'gateway')
attach_standard_error_handlers(app, service="gateway")

# ---- Memory API proxy (browser-safe) --------------------------------------
@app.api_route("/memory/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"])
async def proxy_memory(path: str, request: Request):
    """
    Thin pass-through so the browser can call Memory via the Gateway origin.
    Uses the shared AsyncClient from core_http.client.
    """
    client   = get_http_client()
    upstream = f"{settings.memory_api_url.rstrip('/')}/{path.lstrip('/')}"
    # Pass through query params and a conservative set of headers
    params = dict(request.query_params)
    h_in   = request.headers
    fwd = {}
    for k in [
        "authorization", "content-type", "accept",
        "x-snapshot-etag", "if-none-match", "if-match", "if-modified-since",
        "x-policy", "x-policy-id", "x-policy-key", "x-policy-version",
        "x-user-id", "x-user-roles", "x-sensitivity-ceiling",
        "x-request-id"  # do not forward client x-trace-id; we inject a safe one
    ]:
        v = h_in.get(k) or h_in.get(k.title())
        if v: fwd[k] = v
    body = await request.body()
    # Harden: inject/sanitise tracing context on outbound call
    _headers = inject_trace_context(fwd)
    # memory_api wants an explicit request id; keep caller’s if present, else generate
    _rid = (
        h_in.get("x-request-id")
        or h_in.get("X-Request-Id")
        or generate_request_id()
    )
    # keep both casings – some ASGI stacks normalise to lower, memory_api may read canonical
    _headers["x-request-id"] = _rid
    _headers["X-Request-Id"] = _rid
    resp = await client.request(
        request.method.upper(),
        upstream,
        params=params,
        headers=_headers,
        content=(body or None),
    )
    try:
        # Strategic: proxy audit (no payload), deterministic request_id if present
        _idem = h_in.get("idempotency-key") or h_in.get("Idempotency-Key")
        log_stage(
            logger, "proxy", "memory_passthrough",
            method=request.method.upper(), path=f"/{path}", status=int(resp.status_code),
            idempotency_key=_idem,
            request_id=_rid,
            forwarded_request_id=_rid,
        )
    except (RuntimeError, ValueError, TypeError):
        pass
    # Mirror content/status and return key headers for FE caches/audit
    out = {}
    for k in ["etag", "cache-control", "content-type", "x-snapshot-etag",
              "x-bv-policy-fingerprint", "x-bv-allowed-ids-fp", "x-bv-graph-fp",
              "x-bv-schema-fp"]:
        v = resp.headers.get(k)
        if v: out[k] = v
    return Response(content=resp.content, status_code=resp.status_code, headers=out, media_type=resp.headers.get("content-type"))

# ---------------------------------------------------------------------------
# Startup hooks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _warm_policy_registry() -> None:  # pragma: no cover - startup hook
    try:
        from core_models.policy_registry_cache import fetch_policy_registry  # local import to avoid circular
        from core_config import get_settings as _get_settings
        s = _get_settings()
        registry_url = getattr(s, "policy_registry_url", None)
        if registry_url:
            await fetch_policy_registry()
        else:
            # no-op when no registry configured; avoid success/skip breadcrumbs
            pass
    except (ImportError, AttributeError, RuntimeError, ValueError) as exc:
        try:
            log_stage(logger, "schema", "policy_registry_warm_failed",
                      error=str(exc), request_id="startup")
        except (RuntimeError, ValueError, TypeError):
            pass
        return

@app.on_event("startup")
async def _log_model_impl_selected() -> None:  # pragma: no cover - startup hook
    # Strategic: confirm model implementation source (core_models_gen) in logs for audits
    log_stage(logger, "init", "model_impl_selected",
              impl="core_models_gen", request_id="startup")

# ---- Evidence builder & caches --------------------------------------------
_evidence_builder = EvidenceBuilder()

# ---- Proxy helpers (router / resolver) ------------------------------------
async def route_query(*args, **kwargs):  # pragma: no cover - proxy
    import importlib, sys
    mod = sys.modules.get("gateway.router")
    if mod is None:
        mod = importlib.import_module("gateway.router")
    func = getattr(mod, "route_query")
    return await func(*args, **kwargs)

async def resolve_decision_text(
    text: str,
    *,
    request_id: str | None = None,
    snapshot_etag: str | None = None,
):  # pragma: no cover - proxy
    import importlib
    resolver_mod = importlib.import_module("gateway.resolver")
    resolver_fn = getattr(resolver_mod, "resolve_decision_text")
    return await resolver_fn(text, request_id=request_id, snapshot_etag=snapshot_etag)

# ---- MinIO helpers ---------------------------------------------------------
def _minio_client_or_null():
    # Lazy import to keep tests importable without MinIO
    try:
        from minio import Minio  # type: ignore
    except ImportError as exc:
        log_stage(logger, "artifacts", "minio_unavailable", error=str(exc), request_id="startup")
        return None
    # Strategic breadcrumb: record config once per-process (safe to log; no secrets)
    try:
        log_stage(
            logger, "init", "minio_client_config",
            endpoint=settings.minio_endpoint,
            bucket=settings.minio_bucket,
            secure=bool(settings.minio_secure),
            region=(settings.minio_region or None),
            public_endpoint=(getattr(settings, "minio_public_endpoint", None) or None),
            request_id="startup",
        )
    except (RuntimeError, ValueError, TypeError, OSError):
        pass
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )

def minio_client():
    return _minio_client_or_null()

_bucket_prepared: bool = False

def minio_presign_client():
    """
    Build a MinIO client used *only* for presigning with the public host:port.
    Avoids post-sign URL rewriting (which breaks AWS SigV4).
    Falls back to the internal client if no public endpoint is configured.
    """
    try:
        from minio import Minio  # type: ignore
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    public_ep = (getattr(settings, "minio_public_endpoint", "") or "").strip()
    if not public_ep:
        return minio_client()
    from urllib.parse import urlparse as _urlparse
    pu = _urlparse(public_ep if "://" in public_ep else f"http://{public_ep}")
    return Minio(
        pu.netloc or settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=(pu.scheme == "https"),
        region=settings.minio_region,
    )

def _minio_get_batch(request_id: str) -> dict[str, bytes] | None:
    """Fetch all artifacts for a request from MinIO as a {name: bytes} dict.

    Returns None when MinIO is not configured or nothing found under the prefix.
    Emits strategic logs but never raises to keep call sites simple.
    Also expands named bundle archives (<rid>/bundle_view.tar.gz, <rid>/bundle_full.tar.gz)
    into their constituent files so /v3/bundles/{rid} can always serve JSON, even when
    MinIO only has the archived form.
    """
    try:
        client = minio_client()
        if client is None:
            return None
        prefix = f"{request_id}/"
        # list_objects is a generator
        objects = list(client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True))
        if not objects:
            # Include bucket/prefix so logs are self-explanatory during triage
            log_stage(
                logger, "artifacts", "minio_get_empty",
                request_id=request_id, bucket=settings.minio_bucket, prefix=prefix
            )
            return None
        out: dict[str, bytes] = {}
        archives: list[tuple[str, bytes]] = []
        for obj in objects:
            try:
                resp = client.get_object(settings.minio_bucket, obj.object_name)
                try:
                    data = resp.read()
                finally:
                    resp.close()
                    resp.release_conn()
                # normalise to just the filename part (after the request_id/ prefix)
                name = obj.object_name[len(prefix):] if obj.object_name.startswith(prefix) else obj.object_name
                # keep concrete artifacts as-is
                if not name.endswith(".tar.gz"):
                    out[name] = data
                    continue
                # handle named bundle archives
                base = name[:-7]  # strip ".tar.gz"
                if base in BUNDLE_ARCHIVE_NAMES:
                    archives.append((name, data))
                else:
                    # unknown archive name → keep raw for diagnostics
                    out[name] = data
            except (OSError, RuntimeError, ValueError) as exc:
                # carry on; partial bundles are acceptable but we log them
                log_stage(logger, "artifacts", "minio_get_object_failed",
                          request_id=request_id, object=obj.object_name, error=str(exc))
        # expand any bundle_view / bundle_full archives we found
        for arch_name, arch_bytes in archives:
            try:
                import tarfile
                bio = io.BytesIO(arch_bytes)
                with tarfile.open(fileobj=bio, mode="r:gz") as tf:
                    member_count = 0
                    for member in tf.getmembers():
                        if not member.isreg():
                            continue
                        # we don't need shipping-time helper files in the FE bundle
                        if member.name in ("MANIFEST.json", "README.txt"):
                            continue
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        blob = f.read()
                        # do not overwrite concrete artifacts already present
                        if member.name not in out:
                            out[member.name] = blob
                        member_count += 1
                log_stage(
                    logger,
                    "artifacts",
                    "minio_get_archive_expanded",
                    request_id=request_id,
                    archive=arch_name,
                    files=member_count,
                )
            except (tarfile.TarError, OSError, ValueError) as exc:
                log_stage(logger, "artifacts", "minio_get_archive_expand_failed",
                          request_id=request_id, archive=arch_name, error=str(exc))
        return out or None
    except (OSError, RuntimeError, ValueError, S3Error) as exc:
        log_stage(logger, "artifacts", "minio_get_batch_failed",
                  request_id=request_id, error=str(exc))
        return None

def _load_bundle_dict(request_id: str) -> dict[str, bytes] | None:
    """Load the bundle from object storage (preferred).

    The local in-process LRU is removed; Redis fallback is handled by
    builder (by bundle fingerprint), while this endpoint remains keyed
    by request_id and therefore uses MinIO for durability.
    Returns a {filename: bytes} mapping, or None when not found.
    """
    return _minio_get_batch(request_id)

def _minio_put_batch(request_id: str, artifacts: Mapping[str, bytes]) -> None:
    client = minio_client()
    if client is None:
        # MinIO disabled; skip silently
        return
    global _bucket_prepared
    if not _bucket_prepared:
        try:
            ensure_minio_bucket(
                client,
                bucket=settings.minio_bucket,
                retention_days=settings.minio_retention_days,
            )
            _bucket_prepared = True
        except (OSError, RuntimeError, ValueError) as exc:
            log_stage(logger, "artifacts",
                "minio_bucket_prepare_failed",
                request_id=request_id,
                error=str(exc),
            )
    total_bytes = 0
    for name, blob in list(artifacts.items()):
        client.put_object(
            settings.minio_bucket,
            f"{request_id}/{name}",
            io.BytesIO(blob),
            length=len(blob),
            content_type="application/json",
        )
        metric_counter("gateway_artifact_bytes_total", len(blob), artifact=name)
        total_bytes += len(blob)

    # Strategic success log for auditability and sizing telemetry
    log_stage(logger, "artifacts", "minio_put_batch_ok",
              request_id=request_id, count=len(artifacts), bytes_total=total_bytes)

    # Build and upload named bundles (view/full) and sidecar index
    try:
        # Ensure _meta.json is available in artifacts
        if "_meta.json" not in artifacts:
            pass
        # Build (view/full) named bundles strictly by taxonomy
        bundle_map = {
            "bundle_view": [fn for fn in VIEW_BUNDLE_FILES if fn in artifacts],
            "bundle_full": sorted(list(artifacts.keys())),
        }
        _t_bundle = time.perf_counter()
        named_bundles, meta_bytes = build_named_bundles(artifacts, bundle_map)
        upload_named_bundles(client, settings.minio_bucket, request_id, named_bundles, meta_bytes)
        try:
            # Audit-only timing (cannot retrofit into already-sent meta)
            log_stage(logger, "artifacts", "bundle_write_ms", request_id=request_id,
                      latency_ms=int((time.perf_counter() - _t_bundle) * 1000))
        except (RuntimeError, ValueError, TypeError):
            pass
    except (OSError, RuntimeError, ValueError) as exc:
        # Non-fatal, emit structured warning and continue
        log_stage(logger, "artifacts", "index_build_or_upload_failed", request_id=request_id, error=str(exc))

async def _minio_put_batch_async(
    request_id: str,
    artifacts: Mapping[str, bytes],
    timeout_sec: float | None = None,
) -> None:
    """Upload artifacts off the hot path with a hard timeout."""
    timeout_sec = timeout_sec or settings.minio_async_timeout
    loop = asyncio.get_running_loop()
    try:
        import contextvars  # local import to avoid cost on cold paths
        ctx = contextvars.copy_context()
        func = functools.partial(_minio_put_batch, request_id, artifacts)
        wrapped = lambda: ctx.run(func)
        await asyncio.wait_for(
            loop.run_in_executor(None, wrapped),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log_stage(logger,
            "artifacts",
            "minio_put_batch_timeout",
            request_id=request_id,
            timeout_ms=int(timeout_sec * 1000),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        log_stage(logger,
            "artifacts",
            "minio_put_batch_failed",
            request_id=request_id,
            error=str(exc),
        )

# ---- Ops & metrics endpoints ----------------------------------------------
@app.post("/ops/minio/ensure-bucket")
def ensure_bucket():
    return ensure_minio_bucket(minio_client(),
                               bucket=settings.minio_bucket,
                               retention_days=settings.minio_retention_days)

# ---- Health endpoints ------------------------------------------------------
async def _readiness() -> dict[str, str]:
    return {
        "status": "ready" if await _ping_memory_api() else "degraded",
        "request_id": generate_request_id(),
    }
attach_health_routes(
    app,
    checks={
        "liveness": lambda: {"status": "ok"},
        "readiness": _readiness,
    },
)

# ---- Signature verification API -------------------------------------------
@router.post("/verify", include_in_schema=False)
async def verify_bundle(body: dict):
    """
    Verify a posted pair: { "response": <envelope>, "receipt": <signature> }.
    Optional extras: "trace", "manifest".
    """
    from .validator import run_validator as _run_validator
    from core_utils import jsonx
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_json")
    missing: list[str] = []
    if "response" not in body:
        missing.append("response")
    if "receipt" not in body:
        missing.append("receipt")
    if missing:
        _rid = generate_request_id()
        # strategic logging: FE/script can see exactly which field was missing
        log_stage(
            logger, "verify", "missing_required_fields",
            request_id=_rid,
            missing=missing,
        )
        raise HTTPException(status_code=400, detail="missing_required_fields")

    envelope = body.get("response") or {}
    receipt  = body.get("receipt") or {}

    # envelope is expected to be { "response": { "meta": { "request_id": ... } } }
    # but be defensive – validation callers (like your script) may send just {}
    _env_resp = envelope.get("response") if isinstance(envelope, dict) else {}
    _env_meta = _env_resp.get("meta") if isinstance(_env_resp, dict) else {}
    rid = (
        (_env_meta.get("request_id") if isinstance(_env_meta, dict) else None)
        or generate_request_id()
    )
    artifacts: dict[str, bytes] = {
        "response.json": jsonx.dumps(envelope).encode("utf-8"),
        "receipt.json":  jsonx.dumps(receipt).encode("utf-8"),
    }
    if isinstance(body.get("trace"), dict):
        artifacts["trace.json"] = jsonx.dumps(body["trace"]).encode("utf-8")
    if isinstance(body.get("manifest"), dict):
        artifacts["bundle.manifest.json"] = jsonx.dumps(body["manifest"]).encode("utf-8")
    report = _run_validator(envelope, artifacts=artifacts, request_id=rid)
    log_stage(
        logger, "verify", "report_ready",
        passed=bool(report.get("pass")), checks=len(report.get("checks", [])),
        request_id=rid,
        has_envelope=isinstance(envelope, dict),
    )
    # Surface a concise HTTP-level status: 200 on pass, 422 otherwise.
    status = 200 if report.get("pass") else 422
    return JSONResponse(content=report, status_code=status)

@router.post("/verify/upload", include_in_schema=False)
async def verify_upload(
    response_json: UploadFile = File(...),
    receipt_json: UploadFile  = File(...),
    trace_json: UploadFile | None = File(None),
    bundle_manifest_json: UploadFile | None = File(None),
):
    """
    Verify multipart upload of response.json + receipt.json (+ optional trace/manifest).
    """
    from .validator import run_validator as _run_validator
    import io
    env_bytes = await response_json.read()
    rec_bytes = await receipt_json.read()
    artifacts: dict[str, bytes] = {
        "response.json": env_bytes,
        "receipt.json":  rec_bytes,
    }
    if trace_json:
        artifacts["trace.json"] = await trace_json.read()
    if bundle_manifest_json:
        artifacts["bundle.manifest.json"] = await bundle_manifest_json.read()
    try:
        envelope = jsonx.loads(env_bytes)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="invalid_response_json")
    rid = ((envelope.get("response") or {}).get("meta", {}) or {}).get("request_id", "") or generate_request_id()
    report = _run_validator(envelope, artifacts=artifacts, request_id=rid)
    status = 200 if report.get("pass") else 422
    return JSONResponse(content=report, status_code=status)

# ---- Streaming helper ------------------------------------------------------
def _traced_stream(text: str, include_event: bool = False):
    # Keep the streaming generator inside a span for exemplar + audit timing
    with trace_span("gateway.stream", logger=logger, stage="stream").ctx():
        yield from stream_chunks(text, include_event=include_event)


# ---- API models ------------------------------------------------------------
class AskIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    intent: str = Field(default="why_decision")
    anchor_id: str | None = Field(
        default=None,
    )
    decision_ref: str | None = Field(default=None, exclude=True)

    evidence: Optional[WhyDecisionEvidence] = None
    answer:   Optional[WhyDecisionAnswer]   = None
    policy_id: Optional[str] = None
    graph: dict | None = None
    prompt_id: Optional[str] = None
    request_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_decision_ref(cls, data):
        if isinstance(data, dict) and "anchor_id" not in data:
            if "decision_ref" in data:
                data["anchor_id"] = data["decision_ref"]
            elif "node_id" in data:
                data["anchor_id"] = data["node_id"]
        return data

    @model_validator(mode="after")
    def _validate_minimum_inputs(self):
        """
        Ensure callers supply *either* a full evidence bundle *or* an
        ``anchor_id``.  Do **not** inject an empty stub bundle – that
        prevents the EvidenceBuilder from gathering real neighbours and
        breaks backlink-derivation (spec §B2, roadmap M3).
        """
        if self.evidence is None and not (self.anchor_id or self.decision_ref):
            raise ValueError("Either 'evidence' or 'anchor_id' required")
        return self

class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    question: str = Field(..., min_length=1)
    anchor: str | None = None
    policy: dict | None = None
    graph: dict | None = None

# ---- /v3/query -------------------------------------------------------------
@router.post("/query", response_model=WhyDecisionResponse)
async def v3_query(
    request: Request,
    req: QueryRequest,
    stream: bool = Query(False),
    include_event: bool = Query(False),
    fresh: bool = Query(False),
    template: str | None = Query(default=None, pattern="^[a-z0-9._-]+$"),
    org: str | None = Query(default=None, pattern="^[A-Za-z0-9._-]+$"),
):
    if should_load_shed():
        ra = getattr(settings, "load_shed_retry_after_seconds", 1)
        return JSONResponse(status_code=429, headers={"Retry-After": str(ra)},
                            content={"detail":"Service overloaded","meta":{"load_shed":True}})

    q = (req.question or '').strip()
    if not q:
        raise HTTPException(status_code=400, detail="missing query text")
    
    # Deterministic / replay / correlation id (same path+query → same id)
    _corr_id = compute_request_id(str(request.url.path), request.url.query, None)

    # Per-run request id (this is what we store in MinIO and what FE must use)
    req_id = generate_request_id()
    # Bind once so all downstream log_stage() calls correlate automatically
    bind_request_id(req_id)
    log_stage(logger, "request_id", "bound", request_id=req_id)

    # Idempotency request fingerprint (path + query + body)
    try:
        _body_bytes = jsonx.dumps(req.model_dump(mode="json", exclude_none=True)).encode()
    except (TypeError, ValueError):
        _body_bytes = b""
    # Representation-invariance: treat explicit empty JSON object as empty for request-fingerprint
    _b = _body_bytes.lstrip()
    if _b[:1] == b"{":
        try:
            import orjson as _orjson
            if _orjson.loads(_body_bytes) == {}:
                log_stage(logger, "request_id", "body_empty_json_normalized", request_id=req_id)
                _body_bytes = b""
        except _orjson.JSONDecodeError:
            pass
    # use the correlation id as the request-scope fp for idem/cache
    _req_fp = _corr_id
    log_stage(logger, "request", "request_bound", request_id=req_id)

    # ---- Shape-based behavior: anchored vs. resolver ------------------------
    stage_times: dict[str, int] = {}
    anchor: dict | None = None
    policy_hdrs = _extract_policy_headers(request)

    if (req.anchor or "").strip():
        anchor_id = (req.anchor or "").strip()
        anchor = {"id": anchor_id}
    else:
        # Unanchored shape: resolve text → ranked anchors via Memory (no offline fallback)
        t_intent = time.perf_counter()
        try:
            from .resolver import search_candidates as _resolve_candidates
        except ImportError:
            raise HTTPException(status_code=500, detail="resolver unavailable")
        k = int(getattr(settings, "resolver_top_k", 24))
        matches = await _resolve_candidates(
            q, k=k, request_id=req_id, snapshot_etag=None, policy_headers=policy_hdrs
        )
        try:
            stage_times["intent_resolve"] = int((time.perf_counter() - t_intent) * 1000)
        except (OverflowError, TypeError, ValueError):
            pass
        # Deterministic selection: 0→404, 1→select, >=2→margin test else 409
        if not matches:
            raise HTTPException(status_code=404, detail={'detail': 'no anchor found', 'candidates': []})
        if len(matches) == 1:
            anchor = {'id': matches[0].get('id')}
        else:
            s0 = float(matches[0].get('score') or 0.0)
            s1 = float(matches[1].get('score') or 0.0)
            margin = float(getattr(settings, "rerank_margin", 1e-6))
            if (s0 - s1) > margin:
                anchor = {'id': matches[0].get('id')}
            else:
                cand = [{'id': m.get('id'), 'score': m.get('score')} for m in matches[:5]]
                raise HTTPException(
                    status_code=409,
                    detail={'detail': 'multiple anchors', 'candidates': cand}
                )

    # ---- Idempotency replay / resume (client header: Idempotency-Key) ----------
    prev_fp = None
    _idem_hdr = request.headers.get("Idempotency-Key") or request.headers.get("x-idempotency-key")
    _idem_key = idem_redis_key(_idem_hdr, service="gateway", version=2) if _idem_hdr else None
    _idem_fp  = idem_key_fp(_idem_hdr) if _idem_hdr else None
    _policy_fp = request.headers.get(BV_POLICY_FP)
    _snapshot  = request.headers.get(REQUEST_SNAPSHOT_ETAG)
    body_obj = None

    # Build a strict request-scope fingerprint to guard idempotent replay/merge
    try:
        try:
            body_obj = await request.json()
        except (ValueError, TypeError, RuntimeError):
            body_obj = None
        _scope_fp = compute_request_scope_fp(
            method=request.method,
            path_or_template=str(request.url.path),
            query=request.query_params,
            body=(None if (isinstance(body_obj, dict) and not body_obj) else body_obj),
            snapshot_etag=_snapshot,
            policy_fp=_policy_fp,
        )
    except (TypeError, ValueError):
        # Fallback: mirror the same {} ≡ None policy
        _scope_fp = compute_request_id(
            str(request.url.path),
            request.url.query,
            None if (isinstance(body_obj, dict) and not body_obj) else (body_obj or None),
        )
    if _idem_key:
        rc = get_redis_pool()
        if rc is not None:
            try:
                rec = await idem_get(rc, _idem_key)
                # HARDEN: reject reuse of the key for a different request payload
                if isinstance(rec, dict):
                    _stored_fp = rec.get("request_fingerprint")
                    _stored_scope = rec.get("request_scope_fp")
                    if (_stored_fp and _stored_fp != _req_fp) or (_stored_scope and _stored_scope != _scope_fp):
                        # Strategic breadcrumb for audit drawer; no PII, fingerprints only
                        try:
                            log_stage(logger, "idem", "idem.scope_conflict.replay",
                                      key_fp=_idem_fp or "", stored_req_fp=_stored_fp, incoming_req_fp=_req_fp,
                                      stored_scope_fp=_stored_scope, incoming_scope_fp=_scope_fp, request_id=req_id)
                        except (RuntimeError, ValueError, TypeError):
                            pass
                        return JSONResponse(
                            status_code=409,
                            content={"error": "Idempotency key reused with a different request.",
                                     "hint": "Generate a new Idempotency-Key for each unique request."}
                        )
                # Full replay if a completed outcome exists
                if isinstance(rec, dict) and rec.get("status") == "complete" and isinstance(rec.get("response"), dict):
                    idem_log_replay(logger, key_fp=_idem_fp or "", request_id=req_id)
                    # v3: do not mirror any envelope/meta fields into headers on replay
                    log_stage(logger, "headers", "passthrough_only", request_id=req_id)
                    _h = {"x-request-id": req_id, "Cache-Control": "no-cache"}
                    _sfp = _schema_fp()
                    if _sfp:
                        _h["X-BV-Schema-FP"] = _sfp
                    return JSONResponse(
                        status_code=200,
                        content=rec["response"],
                        headers=_h,
                    )
                # Resume: seed SWR with prior bundle_fp if present
                _p = rec.get("progress") if isinstance(rec, dict) else None
                if isinstance(_p, dict):
                    _prev_bfp = _p.get("bundle_fp")
                    if _prev_bfp:
                        prev_fp = str(_prev_bfp)
                        idem_log_resume_seed(logger, key_fp=_idem_fp or "", bundle_fp=prev_fp)
                # Ensure at least a pending record exists for this key
                if not rec:
                    await idem_set(
                        rc, _idem_key,
                        {
                            "status": "pending",
                            "request_id": req_id,
                            "path": str(request.url.path),
                            "started_at": int(time.time()),
                            "request_fingerprint": _req_fp,
                            "request_scope_fp": _scope_fp, "policy_fp": _policy_fp, "snapshot_etag": _snapshot,
                        }
                    )
                    idem_log_pending(logger, key_fp=_idem_fp or "", request_id=req_id)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                # Emit structured error; keep request flowing
                log_stage(logger, "idem", "idem.error", request_id=req_id, error=type(exc).__name__)

    # -------- SWR fast-path for bv:gw:v1:bundle:{bundle_fp} (opt-in via X-Bundle-FP) ----
    try:
        prev_fp = prev_fp or (request.headers.get("X-Bundle-FP") or request.headers.get("x-bundle-fp"))
        if prev_fp:
            rc = get_redis_pool()
            if rc is not None:
                k = cache_keys.bundle(str(prev_fp))
                log_stage(logger, "cache", "get", layer="bundle", cache_key=k)
                cached = rc.get(k)
                cached = await cached if inspect.isawaitable(cached) else cached
                if cached:
                    log_stage(logger, "cache", "hit", layer="bundle", cache_key=k)
                    try:
                        ttl = rc.ttl(k); ttl = await ttl if inspect.isawaitable(ttl) else ttl
                        if isinstance(ttl, int) and ttl >= 0 and ttl < max(1, int(TTL_BUNDLE_CACHE_SEC * 0.2)):
                            # background refresh keyed by evidence (anchor + policy headers)
                            async def _refresh():
                                await _evidence_builder.build(anchor["id"], fresh=True, policy_headers=policy_hdrs)
                            asyncio.create_task(_refresh())
                            log_stage(logger, "bundle", "swr_refresh_scheduled", cache_key=k, ttl=ttl)
                    except (AttributeError, TypeError, ValueError, OSError):
                        # SWR is best-effort; continue on any non-critical error
                        pass
                    # Serve cached bundle immediately
                    obj = jsonx.loads(cached)
                    return JSONResponse(status_code=200, content=obj)
                else:
                    log_stage(logger, "cache", "miss", layer="bundle", cache_key=k)
    except (AttributeError, TypeError, ValueError, OSError):
        # SWR is best-effort; continue on any error
        pass

    # --- Plan routing (no neighbor side-channel required in v3 edges-only) ---
    functions: list[dict] = []

    # Use the router proxy defined at the top of this module
    routing_info: dict = {}
    try:
        route_result = (await route_query(q, functions)) if functions else {}
        routing_info = route_result if isinstance(route_result, dict) else {}
    except (RuntimeError, ValueError, TypeError) as e:
        log_stage(logger, "router", "router_failed", error=type(e).__name__)
    log_stage(logger, "gateway", "intent_completed", **routing_info)

    # STAGE 5: expand_raw with robust timing
    ev = None
    # Opportunistic evidence cache READ if caller supplies the composite key parts
    if not fresh:
        # Requests MUST provide the request header form only (deck invariant).
        _etag = request.headers.get(REQUEST_SNAPSHOT_ETAG)
        _policy_fp = (
            request.headers.get(BV_POLICY_FP)
        )
        _allowed_ids_fp = request.headers.get(BV_ALLOWED_IDS_FP)
        if _etag and _policy_fp and _allowed_ids_fp:
            rc = get_redis_pool()
            if rc is not None:
                k = cache_keys.evidence(str(_etag), str(_allowed_ids_fp), str(_policy_fp))
                log_stage(logger, "cache", "get", layer="evidence", cache_key=k)
                cached = rc.get(k)
                cached = await cached if inspect.isawaitable(cached) else cached
                if cached:
                    log_stage(logger, "cache", "hit", layer="evidence", cache_key=k)
                    obj = jsonx.loads(cached)
                    try:
                        ev = WhyDecisionEvidence.model_validate(obj)
                    except (TypeError, ValueError, AttributeError):
                        ev = WhyDecisionEvidence(**(obj if isinstance(obj, dict) else {}))
                else:
                    log_stage(logger, "cache", "miss", layer="evidence", cache_key=k)

    if ev is None:
        t_expand = time.perf_counter()
        try:
            ev = await _evidence_builder.build(
                anchor["id"],
                fresh=fresh,
                policy_headers=policy_hdrs,
            )
        finally:
            try:
                stage_times["expand_raw"] = int((time.perf_counter() - t_expand) * 1000)
            except (OverflowError, TypeError, ValueError):
                pass
    else:
        # cache hit: treat expand time as ~0 for telemetry
        stage_times["expand_raw"] = 0
    # Deterministic budget gate (LLM-free) — prompt-only.
    # Responsibility boundary: orchestrator computes a prompt view without mutating the public bundle view.
    try:
        envelope = {"policy": (req.policy or {})}
        gate_plan, ev_prompt = budget_run_gate(
            envelope=envelope,
            evidence_obj=ev,
            request_id=req_id,
        )
        # Attach prompt-view helpers onto the full evidence; do NOT overwrite ev.graph
        try:
            setattr(ev, "_prompt_graph", getattr(ev_prompt, "graph", None))
            setattr(ev, "_budget_cfg_fp", getattr(ev_prompt, "_budget_cfg_fp", None))
            setattr(ev, "_cited_ids_gate", getattr(ev_prompt, "_cited_ids_gate", None))
            setattr(ev, "_events_ranked_top", getattr(ev_prompt, "_events_ranked_top", None))
        except (AttributeError, TypeError):
            pass
    except (RuntimeError, ValueError, TypeError, AttributeError) as e:
        # Surface deterministic breadcrumb, then fail-closed
        log_stage(logger, "budget", "gate_failed", request_id=req_id, error=type(e).__name__)
        raise raise_http_error(500, ErrorCode.internal, "budget gate failed", request_id=req_id)
    if fresh:
        try:
            log_stage(logger, "cache", "bypass", request_id=req_id or "", source="query", reason="fresh=true")
        except (RuntimeError, ValueError, TypeError):
            pass

    # Prefer explicit query params, then headers (both optional). Deterministic and fail-closed downstream.
    _hdr_tmpl = request.headers.get("X-BV-Answer-Template") or None
    _hdr_org  = request.headers.get("X-BV-Org") or None
    selected_template = template or _hdr_tmpl or None
    selected_org      = org or _hdr_org or None

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
        request_id=req_id,
        template_id=selected_template,
        org=selected_org,
    )
    resp, artifacts, req_id = await build_why_decision_response(
        ask_payload, _evidence_builder, stage_times=stage_times,
        source="query",
        fresh=fresh,
        policy_headers=policy_hdrs,
        gateway_plan=gate_plan,
    )
    # v3 hardening: ensure meta.request_id is always present and matches uploads/prefixes
    try:
        if isinstance(resp.meta, dict) and not resp.meta.get("request_id"):
            resp.meta["request_id"] = req_id
            log_stage(logger, "meta", "request_id_injected", request_id=req_id)
    except (TypeError, ValueError, AttributeError):
        pass
    # Ship artifacts to MinIO asynchronously so download endpoints can find them.
    try:
        _count = len(artifacts or {})
        if _count:
            # For interactive FE downloads we prefer to block until objects are visible
            await _minio_put_batch_async(req_id, artifacts)
            log_stage(logger, "artifacts", "minio_put_batch_scheduled",
                      request_id=req_id, count=_count)
        else:
            log_stage(logger, "artifacts", "minio_put_batch_skipped_empty",
                      request_id=req_id)
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        # Non-fatal scheduling error; request should still proceed.
        log_stage(logger, "artifacts", "minio_put_batch_schedule_failed",
                  request_id=req_id, error=type(exc).__name__)
    # Idempotency: record progress (bundle_fp) once known (best-effort)
    if _idem_key:
        try:
            rc = get_redis_pool()
            if rc is not None and isinstance(resp.meta, dict):
                bundle_fp = resp.meta.get("bundle_fp")
                if bundle_fp:
                    # Guard merge by scope to avoid cross-request bleed
                    _rec = await idem_get(rc, _idem_key)
                    if isinstance(_rec, dict) and _rec.get("request_scope_fp") == _scope_fp:
                        await idem_merge(
                            rc, _idem_key, {"progress": {"bundle_fp": str(bundle_fp)}},
                            expected_scope_fp=_scope_fp
                        )
                        idem_log_progress(logger, key_fp=_idem_fp or "", bundle_fp=str(bundle_fp))
                    else:
                        try:
                            log_stage(
                                logger, "idem", "idem.scope_conflict.merge",
                                key_fp=_idem_fp or "",
                                stored_scope_fp=((_rec or {}).get("request_scope_fp")),
                                incoming_scope_fp=_scope_fp, request_id=req_id
                            )
                        except (RuntimeError, ValueError, TypeError):
                            pass
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            log_stage(logger, "idem", "idem.error", request_id=req_id, error=type(exc).__name__)
    # Write-through cache for bundle:{bundle_fp} so future SWR can serve
    rc = get_redis_pool()
    if rc is not None and isinstance(resp.meta, dict):
        bundle_fp = resp.meta.get("bundle_fp")
        if bundle_fp:
            key = cache_keys.bundle(str(bundle_fp))
            payload = resp.model_dump(mode="json", by_alias=True)
            await rc.setex(key, int(TTL_BUNDLE_CACHE_SEC), jsonx.dumps(payload))
            log_stage(logger, "cache", "store", layer="bundle",
                      cache_key=key, ttl=int(TTL_BUNDLE_CACHE_SEC))
    # Decide streaming mode based on query flag or Accept header (SSE)
    want_stream = bool(stream) or ("text/event-stream" in (request.headers.get("accept","").lower()))
    try:
        log_stage(logger, "stream", "mode_selected", request_id=req_id,
                  want_stream=want_stream,
                  reason=("accept" if want_stream and not stream else ("flag" if stream else "off")))
    except (RuntimeError, ValueError, TypeError): pass
    if want_stream:
        headers = {"Cache-Control": "no-cache", "X-Request-Id": req_id}
        log_stage(logger, "headers", "passthrough_only", request_id=req_id)
        # Emit tokens and then the full final response envelope; mirror snapshot ETag to headers
        final_payload = jsonx.sanitize(resp.model_dump(mode="python"))  # WhyDecisionResponse
        # Ensure request_id is mirrored into the final envelope's meta for FE adoption.
        try:
            _m = (final_payload or {}).get("meta", {}) or {}
            if not _m.get("request_id"):
                # FE must use the storage/run id – not the deterministic one
                _m["request_id"] = req_id
            # keep the deterministic one for audit / replay
            if _corr_id:
                _m.setdefault("correlation_id", _corr_id)
            final_payload["meta"] = _m
        except (RuntimeError, ValueError, TypeError, KeyError):
            pass
        envelope = { "schema_version": "v3", "response": final_payload }
        # Idempotency: store final response and mark complete (streaming)
        if _idem_key:
            try:
                rc = get_redis_pool()
                if rc is not None:
                    _rec = await idem_get(rc, _idem_key)
                    if isinstance(_rec, dict) and _rec.get("request_scope_fp") == _scope_fp:
                        _rec.update({
                            "status": "complete",
                            "completed_at": int(time.time()),
                            "request_id": req_id,
                            "response": envelope,
                        })
                        await idem_set(rc, _idem_key, _rec)
                        idem_log_complete(logger, key_fp=_idem_fp or "", request_id=req_id, mode="stream")
                    else:
                        try:
                            log_stage(logger, "idem", "idem.scope_conflict.complete",
                                      key_fp=_idem_fp or "", stored_scope_fp=((_rec or {}).get("request_scope_fp")),
                                      incoming_scope_fp=_scope_fp, request_id=req_id)
                        except (RuntimeError, ValueError, TypeError):
                            pass
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                log_stage(logger, "idem", "idem.error", request_id=req_id, error=type(exc).__name__)

        try:
            log_stage(logger, "request", "v3_query_end", request_id=req_id)
        except (RuntimeError, ValueError, TypeError):
            pass
        try:
            log_stage(
                logger, "stream", "stream.start",
                request_id=req_id,
                policy_fp=envelope.get("meta", {}).get("policy_fp"),
                bundle_fp=envelope.get("meta", {}).get("bundle_fp"),
            )
        except (RuntimeError, ValueError, TypeError):
            pass
        # Compose headers including schema + policy fingerprints (if available)
        _hdrs = dict(headers or {"Cache-Control": "no-cache"})
        _sfp = _schema_fp()
        if _sfp:
            _hdrs["X-BV-Schema-FP"] = _sfp
        # Mirror effective policy/ids/snapshot so FE can detect mismatch early
        try:
            # final_payload is the WhyDecisionResponse (dict) we just built
            _meta = (final_payload or {}).get("meta", {}) or {}
            _pfp = _meta.get("policy_fp")
            _aid_fp = _meta.get("allowed_ids_fp")
            _etag = _meta.get("snapshot_etag")
            _bfp = _meta.get("bundle_fp")
            if _bfp:
                _hdrs["X-BV-Bundle-FP"] = str(_bfp)
            if _pfp:
                _hdrs[BV_POLICY_FP] = str(_pfp)
            if _aid_fp:
                _hdrs[BV_ALLOWED_IDS_FP] = str(_aid_fp)
            if _etag:
                _hdrs[RESPONSE_SNAPSHOT_ETAG] = str(_etag)
        except (RuntimeError, ValueError, TypeError, KeyError):
            # Never fail streaming due to header decoration
            pass
        def _with_logs():
            try:
                log_stage(logger, "query", "stream_open", request_id=req_id)
                # token stream + final envelope
                yield from stream_answer_with_final(
                    resp.answer.short_answer,
                    envelope,
                    include_event=include_event,
                )
            except (RuntimeError, ValueError, TypeError, OSError) as _exc:
                log_stage(logger, "query", "stream_error", request_id=req_id, error=type(_exc).__name__)
                raise
            finally:
                log_stage(logger, "query", "stream_close", request_id=req_id)
        return StreamingResponse(
            _with_logs(),
            media_type="text/event-stream",
            headers=_hdrs,
        )
    if routing_info:
        resp.meta.update(
            {
                "function_calls": routing_info.get("function_calls"),
                "routing_confidence": routing_info.get("routing_confidence"),
                "routing_model_id": routing_info.get("routing_model_id"),
            }
        )
    try:
        log_stage(logger, "request", "v3_query_end", request_id=req_id)
    except (RuntimeError, ValueError, TypeError):
        pass
    env = {}
    try:
        env = jsonx.loads(artifacts.get("response.json", b"{}"))
    except (ValueError, TypeError):
        env = {}
    # Idempotency: store final response and mark complete (JSON)
    if _idem_key:
        try:
            rc = get_redis_pool()
            if rc is not None:
                _rec = await idem_get(rc, _idem_key)
                if isinstance(_rec, dict) and _rec.get("request_scope_fp") == _scope_fp:
                    _rec.update({
                        "status": "complete",
                        "completed_at": int(time.time()),
                        "request_id": req_id,
                        "response": env,
                    })
                    await idem_set(rc, _idem_key, _rec)
                    idem_log_complete(logger, key_fp=_idem_fp or "", request_id=req_id, mode="json")
                else:
                    try:
                        log_stage(
                            logger, "idem", "idem.scope_conflict.complete",
                            key_fp=_idem_fp or "", stored_scope_fp=((_rec or {}).get("request_scope_fp")),
                            incoming_scope_fp=_scope_fp, request_id=req_id
                        )
                    except (RuntimeError, ValueError, TypeError):
                        pass
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            log_stage(logger, "idem", "idem.error", request_id=req_id, error=type(exc).__name__)
    # Mirror graph fingerprint for FE cache keys (parity with Memory)
    headers = {"X-Request-Id": req_id}
    try:
        meta = ((env.get("response") or {}).get("meta") or {})
        fps  = (meta.get("fingerprints") or {})
        gfp  = fps.get("graph_fp") or meta.get("graph_fp")
        if gfp:
            headers[BV_GRAPH_FP] = str(gfp)
        # Also mirror effective policy/ids/snapshot on JSON responses
        pfp = meta.get("policy_fp")
        aid_fp = meta.get("allowed_ids_fp")
        etag = meta.get("snapshot_etag")
        if pfp:
            headers[BV_POLICY_FP] = str(pfp)
        if aid_fp:
            headers[BV_ALLOWED_IDS_FP] = str(aid_fp)
        if etag:
            headers[RESPONSE_SNAPSHOT_ETAG] = str(etag)
    except (AttributeError, TypeError, ValueError):
        # best-effort; omit if envelope shape differs
        pass
    try:
        if "response" in env:
            _m = (env["response"].get("meta") or {}) or {}
            _m.setdefault("request_id", req_id)
            if _corr_id:
                _m.setdefault("correlation_id", _corr_id)
            env["response"]["meta"] = _m
    except (RuntimeError, ValueError, TypeError, KeyError):
        pass
    return JSONResponse(content=env, headers=headers)

# ------------------------------ Prewarm ------------------------------------
class PrewarmIn(BaseModel):
    anchors: List[str] = Field(default_factory=list)
    policy_headers: Optional[dict] = None

@router.post("/prewarm", include_in_schema=False)
async def prewarm(req: PrewarmIn):
    """
    Enqueue pre-warm tasks for provided anchors with optional policy headers.
    Deterministic and fast; uses canonical evidence keys inside builder.
    """
    if not req.anchors:
        raise HTTPException(status_code=400, detail="no anchors provided")
    # de-dup and normalise
    todo = sorted({(a or "").strip() for a in req.anchors if (a or "").strip()})
    for a in todo:
        asyncio.create_task(_evidence_builder.build(a, fresh=False, policy_headers=dict(req.policy_headers or {})))
    log_stage(logger, "prewarm", "scheduled",
              count=len(todo), request_id=generate_request_id())
    return JSONResponse(status_code=202, content={"scheduled": len(todo)})

# ------------------------------ MinIO & Bundles ------------------------------------

@router.get("/ops/minio/ls/{request_id}")
async def ops_minio_ls(
    request_id: str,
    prefix: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
):
    """
    Diagnostic: list objects under a prefix (defaults to f"{request_id}/").
    Same-origin; avoids CORS. Helps verify bucket/endpoint/prefix alignment.
    """
    client = minio_client()
    if client is None:
        raise HTTPException(status_code=503, detail="minio_client_unavailable")
    raw_rid = (request_id or "").strip()
    rid = _normalize_request_id_for_minio(raw_rid)
    pfx = (prefix or f"{rid}/").lstrip("/")
    try:
        it: Iterator = client.list_objects(settings.minio_bucket, prefix=pfx, recursive=True)
        out = []
        for i, obj in enumerate(it, start=1):
            out.append({
                "key": getattr(obj, "object_name", None) or getattr(obj, "key", None),
                "size": getattr(obj, "size", None),
                "last_modified": getattr(obj, "last_modified", None).isoformat() if getattr(obj, "last_modified", None) else None,
                "etag": getattr(obj, "etag", None),
            })
            if i >= limit:
                break
        log_stage(logger, "ops", "ops.minio_ls_ok", request_id=rid, raw_rid=raw_rid, prefix=pfx, count=len(out))
        return {"bucket": settings.minio_bucket, "prefix": pfx, "count": len(out), "objects": out}
    except S3Error as e:
        log_stage(logger, "ops", "ops.minio_ls_error", request_id=rid, raw_rid=raw_rid, prefix=pfx, error=str(e))
        raise HTTPException(status_code=502, detail={"code": "minio_list_failed", "error": str(e)})

app.include_router(router)
@router.get("/ops/minio/config")
async def ops_minio_config():
    """
    Diagnostic: expose the *effective* MinIO settings this process is using.
    Secrets are not returned.
    """
    try:
        cfg = {
            "endpoint": settings.minio_endpoint,
            "bucket": settings.minio_bucket,
            "secure": bool(settings.minio_secure),
            "region": (settings.minio_region or None),
            "public_endpoint": (getattr(settings, "minio_public_endpoint", None) or None),
        }
        log_stage(logger, "ops", "ops.minio_config", **cfg)
        return cfg
    except (OSError, RuntimeError, ValueError, AttributeError) as e:
        raise HTTPException(status_code=500,
                            detail={"code": "minio_config_error", "error": str(e)})

def _normalize_request_id_for_minio(rid: str) -> str:
    """
    FE sometimes sends 32-hex transport/trace ids; objects are written under 16-hex bundle ids.
    Keep the behaviour in the gateway parallel to the FE (see src/utils/bundle.ts).
    """
    r = (rid or "").strip()
    if len(r) == 32:
        nr = r[-16:]
        log_stage(logger, "ops", "rid.normalized", request_id=nr, raw_rid=r)
        return nr
    return r

@router.get("/ops/minio/head/{request_id}")
async def ops_minio_head(request: Request, request_id: str, name: str = Query("bundle_view")):
    """
    Diagnostic: stat an object for a given request_id.
    Now supports *both*:
      - archive names:  bundle_view / bundle_full  →  <rid>/<name>.tar.gz
      - artifact names: receipt.json / response.json / trace.json / bundle.manifest.json → <rid>/<name>
    """
    client = minio_client()
    if client is None:
        raise HTTPException(status_code=503, detail="minio_client_unavailable")

    # FE may send 32-hex; objects are under the 16-hex form → normalise
    raw_rid = (request_id or "").strip()
    rid = _normalize_request_id_for_minio(raw_rid)

    name = (name or "bundle_view").strip()
    raw_path = request.url.path
    raw_query = request.url.query

    if name in BUNDLE_ARCHIVE_NAMES:
        object_name = f"{rid}/{name}.tar.gz"
        kind = "archive"
    else:
        # FE asked for a concrete file
        object_name = f"{rid}/{name}"
        kind = "artifact"
    try:
        _ = client.stat_object(settings.minio_bucket, object_name)
        log_stage(
            logger, "ops", "ops.minio_head_ok",
            request_id=rid,
            raw_rid=raw_rid,
            name=name,
            kind=kind,
            object_key=object_name,
            raw_path=raw_path,
            raw_query=raw_query,
        )
        return {"exists": True, "object": object_name}
    except S3Error as e:
        if getattr(e, "code", "") == "NoSuchKey":
            log_stage(
                logger, "ops", "ops.minio_head_missing",
                request_id=rid,
                raw_rid=raw_rid,
                name=name,
                kind=kind,
                object_key=object_name,
                raw_path=raw_path,
                raw_query=raw_query,
            )
            return {"exists": False, "object": object_name}
        log_stage(logger, "ops", "ops.minio_head_error", request_id=rid, raw_rid=raw_rid, object_key=object_name, error=str(e))
        raise HTTPException(status_code=502, detail={"code": "minio_head_failed", "error": str(e)})

@router.api_route("/bundles/{request_id}/download", methods=["GET","POST"], include_in_schema=False)
async def download_bundle(request: Request, request_id: str, name: str = Query("bundle_view")):
    """Return a presigned URL for the archived bundle when possible.

    Attempts to generate a real presigned GET for `<request_id>/<name>.tar.gz` in MinIO.
    Falls back to the internal `/v3/bundles/{request_id}.tar` proxy if MinIO
    is unavailable, not publicly reachable, or the object is missing.
    """
    # Harden + normalize
    raw_path = request.url.path
    raw_query = request.url.query
    raw_rid = (request_id or "").strip()
    rid = _normalize_request_id_for_minio(raw_rid)
    forwarded = request.headers.get("x-forwarded-uri") or request.headers.get("x-original-uri")
    name = (name or "bundle_view").strip()
    allowed_archives = BUNDLE_ARCHIVE_NAMES
    allowed_artifacts = set(VIEW_ARTIFACT_NAMES)

    is_archive = name in allowed_archives
    is_artifact = name in allowed_artifacts

    if not is_archive and not is_artifact:
        # self-explanatory breadcrumb for request summaries
        log_stage(
            logger, "download", "invalid_name",
            request_id=rid,
            raw_rid=raw_rid,
            name=name,
            allowed=list(allowed_archives),
            allowed_artifacts=list(allowed_artifacts),
            raw_path=raw_path,
            raw_query=raw_query,
            forwarded_uri=forwarded,
        )
        # 422 with structured details (string message + request_id required)
        raise raise_http_error(
            422,
            ErrorCode.validation_failed,
            "invalid_bundle_name",
            request_id=request_id,
            details={
                "invalid_name": name,
                "allowed": list(allowed_archives),
                "allowed_artifacts": list(allowed_artifacts),
                "hint": "Use GET /v3/bundles/{request_id}/receipt.json for the raw receipt"
            },
        )
    # v3: presign named bundles first; fallback to v3 TAR proxy
    expires_sec = 600
    if is_archive:
        url = f"/v3/bundles/{rid}.tar?name={name}"
    else:
        # artifact → direct proxy path
        url = f"/v3/bundles/{rid}/{name}"
    presigned_ok = False
    try:
        client = minio_client()                 # internal I/O (stat)
        presign_client = minio_presign_client() # signs with public host if configured
        if client is not None:
            from datetime import timedelta as _td  # local import
            from urllib.parse import urlparse as _urlparse
            if is_archive:
                object_name = f"{rid}/{name}.tar.gz"
            else:
                object_name = f"{rid}/{name}"
            try:
                _ = client.stat_object(settings.minio_bucket, object_name)
                if presign_client is not None:
                    _url = presign_client.presigned_get_object(
                        settings.minio_bucket,
                        object_name,
                        expires=_td(seconds=expires_sec),
                    )
                    host = _urlparse(_url).netloc
                    url = _url
                    log_stage(
                        logger, "bundle", "download.presigned_minio_ok",
                        request_id=rid, bundle=name, object_key=object_name, host=host
                    )
                    presigned_ok = True
                else:
                    log_stage(
                        logger, "bundle", "download.presigned_minio_unavailable",
                        request_id=rid, bundle=name, object_key=object_name
                    )
            except S3Error as e:
                if getattr(e, "code", "") != "NoSuchKey":
                    log_stage(logger, "bundle", "download.presigned_minio_failed",
                              request_id=rid, bundle=name, object_key=object_name, error=str(e))
                else:
                    log_stage(logger, "bundle", "download.presigned_minio_not_found",
                              request_id=rid, bundle=name, object_key=object_name)
    except (OSError, RuntimeError, ValueError, AttributeError):
        # leave url as fallback
        pass
    # If we could not presign and the batch is not yet visible, surface "pending" for backoff/retry UX.
    if not presigned_ok:
        _probe = _load_bundle_dict(rid)
        if not _probe:
            # enrich with actual keys under the prefix, so FE can see "you asked for 2091..., I only have f068..."
            existing: list[str] = []
            try:
                client = minio_client()
                if client is not None:
                    for obj in client.list_objects(settings.minio_bucket, prefix=f"{rid}/", recursive=False):
                        on = getattr(obj, "object_name", None) or getattr(obj, "key", None)
                        if on:
                            existing.append(on)
            except (OSError, RuntimeError, ValueError, S3Error):
                # best-effort: leave existing empty
                pass
            log_stage(
                logger, "bundle", "pending_upload",
                request_id=rid,
                raw_rid=raw_rid,
                bundle=name,
                bucket=settings.minio_bucket,
                prefix=f"{rid}/",
                existing_keys=existing,
            )
            return JSONResponse(
                status_code=202,
                headers={"Retry-After": "1"},
                content={"status": "pending", "hint": "Artifacts not indexed yet", "fallback": url, "expires_in": expires_sec},
            )
    return JSONResponse(content={"url": url, "expires_in": expires_sec})

# Convenience GET for linkable UIs
@router.get("/bundles/{request_id}/download", include_in_schema=False)
async def download_bundle_get(request: Request, request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    return await download_bundle(request, request_id, name)

@router.get("/bundles/{request_id}", include_in_schema=False)
async def get_bundle(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    """Stream the JSON artifact bundle for a request.
    View bundle exposes only response.json and trace.json (downloadables).
    Each artifact is UTF-8 when possible; otherwise Base64. Returns 404 when unknown."""
    rid = _normalize_request_id_for_minio(request_id)
    bundle = _load_bundle_dict(rid)
    # Filter artifacts for minimal view bundle unless full explicitly requested
    if name == "bundle_view":
        allowed = set(VIEW_BUNDLE_FILES)
        bundle = {k: v for k, v in (bundle or {}).items() if k in allowed}
    if not bundle:
        log_stage(logger, "bundle", "pending_upload",
                  request_id=rid, raw_rid=request_id, hint="No artifacts under prefix; likely async upload still in-flight")
        # Prefer 202 to guide FE into short backoff rather than surfacing 404
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "1"},
            content={
                "status": "pending",
                "error": "bundle_not_found",
                "message": "Artifacts not available yet for this request.",
                "hint": "Retry shortly; check for 'minio_put_batch_ok' / 'index_build_or_upload_failed' in gateway logs.",
            },
        )
    content: dict[str, Any] = {}
    for fname, blob in bundle.items():
        try:
            if isinstance(blob, (bytes, bytearray)):
                try:
                    content[fname] = blob.decode()
                except UnicodeDecodeError:
                    import base64
                    # log the transformation for observability
                    try:
                        log_stage(logger, "bundle", "download.binary_base64", request_id=request_id, file=fname)
                    except Exception as log_exc:  # keep tight: logging must not break download
                        logger.warning(
                            "bundle.download.binary_base64.log_failed",
                            extra={"request_id": request_id, "file": fname, "error": type(log_exc).__name__},
                        )
                    content[fname] = base64.b64encode(blob).decode()
            else:
                content[fname] = blob
        except (ValueError, TypeError, AttributeError):
            content[fname] = None
    try:
        # Log the size of the serialized bundle for metrics
        log_stage(
            logger, "bundle", "download.served",
            request_id=rid,
            bundle=name,  # <-- correctly logs "bundle_view" or "bundle_full"
            size=len(jsonx.dumps(content).encode("utf-8")),
        )
    except (RuntimeError, ValueError, TypeError):
        pass
    try:
        anchor_id = None
        try:
            resp_json = content.get("response.json")
            if isinstance(resp_json, str):
                import json as _json
                _obj = _json.loads(resp_json)
                _root = (_obj.get("response") if ("response" in _obj and "schema_version" in _obj) else _obj) or {}
                anchor_id = ((_root.get("evidence", {}) or {}).get("anchor") or {}).get("id")
        except (ValueError, TypeError):
            anchor_id = None
        from datetime import datetime as _dt, timezone as _tz
        date_str = _dt.now(_tz.utc).strftime("%Y%m%d")
        base = anchor_id or rid
        filename = f"evidence-{base}-{date_str}.json"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    except (ValueError, TypeError):
        headers = {}
    return JSONResponse(content=content, headers=headers)

@router.get("/bundles/{request_id}.tar", include_in_schema=False)
async def get_bundle_tar(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    """Return the artifact bundle as a TAR archive (exec-friendly).
    Pull from hot cache; fall back to MinIO when needed."""
    rid = _normalize_request_id_for_minio(request_id)
    bundle = _load_bundle_dict(rid)
    if not bundle:
        log_stage(logger, "bundle", "pending_upload",
                  request_id=rid, raw_rid=request_id, hint="No artifacts under prefix; likely async upload still in-flight")
        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "1"},
            content={
                "status": "pending",
                "error": "bundle_not_found",
                "message": "Artifacts not available yet for this request.",
                "hint": "Retry shortly; check for 'minio_put_batch_ok' / 'index_build_or_upload_failed' in gateway logs.",
            },
        )

    import tarfile, io, json as _json
    buf = io.BytesIO()
    with tarfile.open(mode="w", fileobj=buf) as tar:
        # individual artifacts
        for fname, blob in bundle.items():
            data = blob if isinstance(blob, (bytes, bytearray)) else str(blob).encode()
            ti = tarfile.TarInfo(name=fname)
            ti.size = len(data)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))
    buf.seek(0)

    from datetime import datetime as _dt, timezone as _tz
    date_str = _dt.now(_tz.utc).strftime("%Y%m%d")
    filename = f"evidence-{rid}-{date_str}.tar"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=buf.getvalue(), headers=headers, media_type="application/x-tar")

@router.get("/bundles/{request_id}/receipt.json", include_in_schema=False)
async def get_bundle_receipt(request_id: str):
    """
    Return the raw receipt.json for a request (404 if missing).
    """
    rid = _normalize_request_id_for_minio(request_id)
    bundle = _load_bundle_dict(rid)
    if not bundle or "receipt.json" not in bundle:
        raise HTTPException(status_code=404, detail="receipt not found")
    # Serve the receipt as-is; clients can parse JSON on the wire.
    return Response(content=bundle["receipt.json"], media_type="application/json")

@router.head("/bundles/{request_id}/receipt.json", include_in_schema=False)
async def head_bundle_receipt(request_id: str):
    """
    Lightweight existence probe used by the FE to avoid opening blank tabs.
    """
    rid = _normalize_request_id_for_minio(request_id)
    bundle = _load_bundle_dict(rid)
    if not bundle or "receipt.json" not in bundle:
        raise HTTPException(status_code=404, detail="receipt not found")
    return Response(status_code=200)

# ---- Final wiring ----------------------------------------------------------
app.include_router(router)

@app.on_event("startup")
async def _start_load_shed_refresher() -> None:
    try:
        log_stage(logger, "init", "sse_helper_selected", sse_module="core_utils.sse")
        start_background_refresh(int(os.getenv("GATEWAY_LOAD_SHED_REFRESH_MS","300")))
    except (RuntimeError, ValueError, OSError):
        pass

@app.on_event("startup")
async def _ensure_minio_bucket_startup() -> None:
    """
    Idempotently ensure the artifacts bucket exists + lifecycle policy is applied.
    This avoids first-click timeouts while the async uploader races bucket creation.
    """
    try:
        ensure_minio_bucket(
            minio_client(),
            bucket=settings.minio_bucket,
            retention_days=settings.minio_retention_days,
        )
        log_stage(logger, "init", "minio_bucket_ensured_startup", bucket=settings.minio_bucket)
    except (OSError, RuntimeError, ValueError, S3Error):
        # Best-effort; ops endpoint still available at /ops/minio/ensure-bucket
        pass

@app.on_event("shutdown")
async def _stop_load_shed_refresher() -> None:
    try:
        stop_background_refresh()
    except (RuntimeError, ValueError, OSError):
        pass
