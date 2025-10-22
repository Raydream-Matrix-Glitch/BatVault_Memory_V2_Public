import asyncio, functools, io, os, time, inspect
from typing import List, Optional, Any, Mapping
from core_utils import jsonx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query
from fastapi.responses import JSONResponse, StreamingResponse
from core_utils.sse import stream_answer_with_final, stream_chunks
from core_utils.fingerprints import schema_dir_fp
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
        return dict(hdrs or {})
from core_models_gen import (
    WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse
)
from core_utils.health import attach_health_routes
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from . import evidence
from .evidence import EvidenceBuilder
from core_utils.load_shed import should_load_shed, start_background_refresh, stop_background_refresh
from .builder import build_why_decision_response
from .budget_gate import run_gate as budget_run_gate
from core_cache.redis_client import get_redis_pool
from core_cache import keys as cache_keys
from core_config.constants import TTL_BUNDLE_CACHE_SEC
from core_http.errors import attach_standard_error_handlers
from core_idem import (
    idem_redis_key, idem_key_fp,
    idem_get, idem_set, idem_merge,
    idem_log_replay, idem_log_pending,
    idem_log_resume_seed, idem_log_progress, idem_log_complete,
    compute_request_scope_fp,
)

# ---- Configuration & globals ----------------------------------------------
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = False

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
    """
    Collect pass-through policy/ACL headers to forward to the Memory API.
    We do not modify or validate values here; ownership stays with memory_api.
    """
    keys = [
        "X-User-Id","X-User-Roles","X-User-Namespaces",
        "X-Policy-Version","X-Policy-Key",
        "X-Request-Id","X-Trace-Id",
        "X-Domain-Scopes","X-Edge-Allow","X-Max-Hops","X-Sensitivity-Ceiling",
        # add snapshot & policy/evidence fingerprints so callers can enable cache hits:
        "X-Snapshot-Etag",
        "X-Policy-Fp","X-Bv-Policy-Fingerprint",
        "X-Allowed-Ids-Fp",
    ]
    hdrs: dict = {}
    for k in keys:
        # Header access is Mapping-like and safe; avoid masking errors
        v = request.headers.get(k) or request.headers.get(k.lower())
        if v is not None:
            hdrs[k] = v
    return hdrs

_LOG_NO_ACTIVE_SPAN = os.getenv('GATEWAY_DEBUG_NO_ACTIVE_SPAN') == '1'

# Files included in the minimal "bundle_view" (schema-driven; Stage 8 taxonomy)# Pull the canonical list directly from the schema (no heuristics).
VIEW_BUNDLE_FILES = list(view_artifacts_order())
# "bundle_full" includes every artifact we persisted (raw + pre/post + envelope/model_raw)

# ---- Application & router --------------------------------------------------
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")
 
# Standardized wiring (observability, health, CORS, rate-limit via env)
setup_service(app, 'gateway')
attach_standard_error_handlers(app, service="gateway")

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

def _minio_get_batch(request_id: str) -> dict[str, bytes] | None:
    """Fetch all artifacts for a request from MinIO as a {name: bytes} dict.

    Returns None when MinIO is not configured or nothing found under the prefix.
    Emits strategic logs but never raises to keep call sites simple.
    """
    try:
        client = minio_client()
        if client is None:
            return None
        prefix = f"{request_id}/"
        # list_objects is a generator
        objects = list(client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True))
        if not objects:
            log_stage(logger, "artifacts", "minio_get_empty", request_id=request_id, prefix=prefix)
            return None
        out: dict[str, bytes] = {}
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
                out[name] = data
            except (OSError, RuntimeError, ValueError) as exc:
                # carry on; partial bundles are acceptable but we log them
                log_stage(logger, "artifacts", "minio_get_object_failed",
                          request_id=request_id, object=obj.object_name, error=str(exc))
        return out or None
    except (OSError, RuntimeError, ValueError) as exc:
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
        metric_counter("artifact_bytes_total", len(blob), artifact=name)
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

# Catch-all exception handler to avoid leaking non-serialisable objects into JSON responses
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    try:
        body = await request.body()
    except (RuntimeError, ValueError, TypeError):
        body = b""
    # Representation-invariance: treat explicit empty JSON object as empty
    _b = body.lstrip()
    if _b[:1] == b"{":
        try:
            import orjson as _orjson
            if _orjson.loads(body) == {}:
                log_stage(logger, "request_id", "body_empty_json_normalized")
                body = b""
        except _orjson.JSONDecodeError:
            pass
    try:
        req_id = compute_request_id(str(request.url.path), request.url.query, body)
    except (ValueError, TypeError):
        req_id = generate_request_id()
    log_stage(logger, "request", "unhandled_exception",
              request_id=req_id, error=str(exc), error_type=exc.__class__.__name__)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL",
                "message": "Unexpected error",
                "details": jsonx.sanitize({"type": exc.__class__.__name__, "message": str(exc)}),
                "request_id": req_id,
            },
            "request_id": req_id,
        },
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
async def verify_envelope(envelope: dict):
    """
    Verify a posted Exec Summary envelope (response.json).
    Returns a validation report including signature verification.
    """
    from .validator import run_validator as _run_validator
    rid = ((envelope.get("response") or {}).get("meta", {}) or {}).get("request_id", "") or generate_request_id()
    report = _run_validator(envelope, artifacts=None, request_id=rid)
    log_stage(
        logger, "verify", "report_ready",
        passed=bool(report.get("pass")), checks=len(report.get("checks", [])),
        request_id=rid
    )
    # Surface a concise HTTP-level status: 200 on pass, 422 otherwise.
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

class _QueryIn_Deprecated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = Field(default=None, alias="text")
    q: str | None = Field(default=None, alias="q")
    functions: list[str | dict] | None = None
    request_id: str | None = None

@router.post("/ask", include_in_schema=False)
async def ask_deprecated(request: Request):
    """
    /v2/ask has been removed. Return 410 Gone with migration hint.
    See BATVAULT_GATEWAY_ACTION_PLAN_v3 §STAGE 2.
    """
    raise HTTPException(
        status_code=410,
        detail="/v2/ask has been removed. Use POST /v2/query with {question, anchor, policy, graph.alias?}."
    )

# ---- /v2/query -------------------------------------------------------------
@router.post("/query", response_model=WhyDecisionResponse)
async def v2_query(
    request: Request,
    req: QueryRequest,
    stream: bool = Query(False),
    include_event: bool = Query(False),
    fresh: bool = Query(False),
):
    if should_load_shed():
        ra = getattr(settings, "load_shed_retry_after_seconds", 1)
        return JSONResponse(status_code=429, headers={"Retry-After": str(ra)},
                            content={"detail":"Service overloaded","meta":{"load_shed":True}})

    q = (req.question or '').strip()
    if not q:
        raise HTTPException(status_code=400, detail="missing query text")
    
    # Deterministic request ID for resolver logs (align with middleware scheme)
    req_id = compute_request_id(str(request.url.path), request.url.query, None)
    # Bind once so all downstream log_stage() calls correlate automatically
    bind_request_id(req_id)

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
    _req_fp = compute_request_id(str(request.url.path), request.url.query, _body_bytes)
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
    _policy_fp = request.headers.get("X-BV-Policy-Fingerprint") or request.headers.get("X-Policy-Fp")
    _snapshot  = request.headers.get("X-Snapshot-Etag")
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
        _etag = request.headers.get("X-Snapshot-Etag") or request.headers.get("x-snapshot-etag")
        _policy_fp = (
            request.headers.get("X-Policy-Fp") or request.headers.get("x-policy-fp")
            or request.headers.get("X-Bv-Policy-Fingerprint") or request.headers.get("x-bv-policy-fingerprint")
        )
        _allowed_ids_fp = request.headers.get("X-Allowed-Ids-Fp") or request.headers.get("x-allowed-ids-fp")
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
                        # pydantic v2
                        ev = WhyDecisionEvidence.model_validate(obj)
                    except Exception:
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
        # Surface deterministic breadcrumb, do not fail request here (validator will catch inconsistencies)
        log_stage(logger, "budget", "gate_failed", request_id=req_id, error=type(e).__name__)
        pass
    if fresh:
        try:
            log_stage(logger, "cache", "bypass", request_id=req_id or "", source="query", reason="fresh=true")
        except (RuntimeError, ValueError, TypeError):
            pass

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
        request_id=req_id,
    )
    resp, artifacts, req_id = await build_why_decision_response(
        ask_payload, _evidence_builder, stage_times=stage_times,
        source="query",
        fresh=fresh,
        policy_headers=policy_hdrs,
    )
    # Idempotency: record progress (bundle_fp) once known (best-effort)
    if _idem_key:
        try:
            rc = get_redis_pool()
            if rc is not None and isinstance(resp.meta, dict):
                bundle_fp = (resp.meta.get("fingerprints") or {}).get("bundle_fp") or resp.meta.get("bundle_fp")
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
        bundle_fp = (resp.meta.get("fingerprints") or {}).get("bundle_fp") or resp.meta.get("bundle_fp")
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
        headers = {"Cache-Control": "no-cache", "x-request-id": req_id}
        log_stage(logger, "headers", "passthrough_only", request_id=req_id)
        # Emit tokens and then the full final response envelope; mirror snapshot ETag to headers
        final_payload = jsonx.sanitize(resp.model_dump(mode="python"))  # WhyDecisionResponse
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
            log_stage(logger, "request", "v2_query_end", request_id=req_id)
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
        # Compose headers including schema fingerprint (if available)
        _hdrs = dict(headers or {"Cache-Control": "no-cache"})
        _sfp = _schema_fp()
        if _sfp:
            _hdrs["X-BV-Schema-FP"] = _sfp
        return StreamingResponse(
            stream_answer_with_final(
                resp.answer.short_answer,  # token stream
                envelope,                  # final envelope (matches response.json)
                include_event=include_event,
            ),
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

    headers = {"x-request-id": req_id}
    try:
        log_stage(logger, "request", "v2_query_end", request_id=req_id)
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

@router.post("/bundles/{request_id}/download", include_in_schema=False)
async def download_bundle(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    """Return a presigned URL for the archived bundle when possible.

    Attempts to generate a real presigned GET for `<request_id>.bundle.tar.gz` in MinIO.
    Falls back to the internal `/v2/bundles/{request_id}.tar` proxy if MinIO
    is unavailable, not publicly reachable, or the object is missing.
    """
    expires_sec = 600
    # Prefer the exec-friendly TAR proxy by default; include name so the proxy can filter
    url = f"/v2/bundles/{request_id}.tar?name={name}"
    try:
        client = minio_client()
        if client is not None:
            try:
                from datetime import timedelta as _td  # local import
                _ = client.stat_object(settings.minio_bucket, f"{request_id}.bundle.tar.gz")
                # If a public endpoint is configured, build a *public* client for presigning
                pub_client = None
                try:
                    pub = (getattr(settings, "minio_public_endpoint", None) or "").strip()
                    if pub:
                        from urllib.parse import urlparse as _uparse
                        _pu = _uparse(pub if "://" in pub else f"http://{pub}")
                        from minio import Minio as _Minio  # type: ignore
                        pub_client = _Minio(
                            endpoint=_pu.netloc,
                            access_key=settings.minio_access_key,
                            secret_key=settings.minio_secret_key,
                            secure=_pu.scheme == "https",
                            region=settings.minio_region,
                        )
                except (ValueError, ImportError, AttributeError) as _exc:
                    log_stage(logger, "bundle", "download.public_client_init_failed", request_id=request_id, error=str(_exc))
                if pub_client:
                    # Presign the *named* bundle object
                    url = pub_client.presigned_get_object(
                        settings.minio_bucket,
                        f"{request_id}/{name}.tar.gz",
                        expires=_td(seconds=expires_sec),
                    )
                    log_stage(logger, "bundle", "download.presigned_minio_public", request_id=request_id, bundle=name)
                else:
                    # No public endpoint configured → keep internal proxy URL (works through API Edge)
                    log_stage(logger, "bundle", "download.fallback_proxy_used", request_id=request_id)
            except Exception as exc:
                # Soft-fail to the internal endpoint
                log_stage(logger, "bundle", "download.presigned_minio_failed", request_id=request_id, error=str(exc))
    except (OSError, RuntimeError, ValueError, AttributeError):
        # leave url as fallback
        pass
    return JSONResponse(content={"url": url, "expires_in": expires_sec})

# Convenience GET alias for linkable UIs
@router.get("/bundles/{request_id}/download", include_in_schema=False)
async def download_bundle_get(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    return await download_bundle(request_id, name)

@router.get("/bundles/{request_id}", include_in_schema=False)
async def get_bundle(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    """Stream the JSON artifact bundle for a request.
    View bundle exposes only response.json and trace.json (downloadables).
    Each artifact is UTF-8 when possible; otherwise Base64. Returns 404 when unknown."""
    bundle = _load_bundle_dict(request_id)
    # Filter artifacts for minimal view bundle unless full explicitly requested
    if name == "bundle_view":
        allowed = set(VIEW_BUNDLE_FILES)
        bundle = {k: v for k, v in (bundle or {}).items() if k in allowed}
    if not bundle:
        raise HTTPException(status_code=404, detail="bundle not found")
    content: dict[str, Any] = {}
    for name, blob in bundle.items():
        try:
            if isinstance(blob, bytes):
                try:
                    content[name] = blob.decode()
                except UnicodeDecodeError:
                    import base64
                    content[name] = base64.b64encode(blob).decode()
            else:
                content[name] = blob
        except (ValueError, TypeError, AttributeError):
            content[name] = None
    try:
        # Log the size of the serialized bundle for metrics
        log_stage(logger, "bundle",
            "download.served",
            request_id=request_id,
            bundle=name,
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
        base = anchor_id or request_id
        filename = f"evidence-{base}-{date_str}.json"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    except (ValueError, TypeError):
        headers = {}
    return JSONResponse(content=content, headers=headers)

@router.get("/bundles/{request_id}.tar", include_in_schema=False)
async def get_bundle_tar(request_id: str, name: str = Query("bundle_view", pattern="^(bundle_view|bundle_full)$")):
    """Return the artifact bundle as a TAR archive (exec-friendly).
    Pull from hot cache; fall back to MinIO when needed."""
    bundle = _load_bundle_dict(request_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="bundle not found")

    import tarfile, io, json as _json
    buf = io.BytesIO()
    with tarfile.open(mode="w", fileobj=buf) as tar:
        # individual artifacts
        for name, blob in bundle.items():
            data = blob if isinstance(blob, (bytes, bytearray)) else str(blob).encode()
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data); ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))
        # MANIFEST.json
        manifest = {"request_id": request_id, "files": sorted(list(bundle.keys()))}
        mbytes = _json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        ti = tarfile.TarInfo(name="MANIFEST.json")
        ti.size = len(mbytes); ti.mtime = int(time.time())
        tar.addfile(ti, io.BytesIO(mbytes))
        # README.txt
        readme = (
            "BatVault evidence bundle\n"
            "========================\n"
            f"request_id: {request_id}\n\n"
            "Contains deterministic artifacts used to generate the answer:\n"
            "- response.json (signed Exec Summary)\n"
            "- trace.json (compact, unsigned)\n"
            "- validator_report.json (internal contract checks)\n"
            "- evidence_pre.json / evidence_canonical.json / plan.json (internal audit)\n"
            "\nOpen MANIFEST.json for the file list.\n"
        ).encode("utf-8")
        ti = tarfile.TarInfo(name="README.txt")
        ti.size = len(readme); ti.mtime = int(time.time())
        tar.addfile(ti, io.BytesIO(readme))
    buf.seek(0)

    from datetime import datetime as _dt, timezone as _tz
    date_str = _dt.now(_tz.utc).strftime("%Y%m%d")
    filename = f"evidence-{request_id}-{date_str}.tar"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=buf.getvalue(), headers=headers, media_type="application/x-tar")

@app.get("/evidence/{decision_ref}", include_in_schema=False)
async def evidence_endpoint(decision_ref: str, **kwargs: Any):
    """
    Legacy evidence endpoint is removed in v3.  Inform callers to use the unified
    POST /v2/query endpoint instead.  We return 410 Gone so clients migrate.
    """
    raise HTTPException(
        status_code=410,
        detail="/evidence has been removed. Use POST /v2/query with {question, anchor} instead.",
    )

# ---- Final wiring ----------------------------------------------------------
app.include_router(router)

@app.on_event("startup")
async def _start_load_shed_refresher() -> None:
    try:
        log_stage(logger, "init", "sse_helper_selected", sse_module="core_utils.sse")
        start_background_refresh(int(os.getenv("GATEWAY_LOAD_SHED_REFRESH_MS","300")))
    except (RuntimeError, ValueError, OSError):
        pass

@app.on_event("shutdown")
async def _stop_load_shed_refresher() -> None:
    try:
        stop_background_refresh()
    except (RuntimeError, ValueError, OSError):
        pass
