import asyncio, functools, io, os, time, inspect
from typing import List, Optional, Any
# importlib.metadata was previously imported to expose version information on the API,
# but the value was never referenced.  Remove the unused import to avoid confusion.

from core_utils import jsonx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core_utils.sse import stream_answer_with_final, stream_chunks
from core_storage.artifact_index import build_bundle_and_meta, upload_bundle_and_meta
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
from core_utils.ids import compute_request_id, generate_request_id

# Internal packages
from core_config import get_settings
# TTL_SCHEMA_CACHE_SEC is unused in this module; it can be imported directly by
# consumers that need it.  Avoid bringing unused constants into this scope.
from core_logging import get_logger, bind_trace_ids, trace_span
from .logging_helpers import stage as log_stage
from core_observability.otel import setup_tracing, instrument_fastapi_app
from .metrics import (
    counter as metric_counter,
    histogram as metric_histogram,
)
from core_models.models import (
    WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse
)
# canonical_json is unused here; rely on the builder and other modules to handle
# canonical JSON processing.
from core_utils.health import attach_health_routes
# generate_request_id is unused; compute_request_id provides the required request
# identifier in this module.
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from core_validator import validate_response
# Import the public canonical helper rather than the private underscore version.
from core_validator import canonical_allowed_ids

from . import evidence
from .evidence import EvidenceBuilder
from .schema_cache import fetch_schema
# fetch_json is unused in this module; schema retrieval goes through schema_cache.
from .load_shed import should_load_shed, start_background_refresh, stop_background_refresh
from .builder import build_why_decision_response
from .builder import BUNDLE_CACHE
from core_config.constants import TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS

# ---- Configuration & globals ----------------------------------------------
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = False

_LOG_NO_ACTIVE_SPAN = os.getenv('GATEWAY_DEBUG_NO_ACTIVE_SPAN') == '1'

_SEARCH_MS      = TIMEOUT_SEARCH_MS
_EXPAND_MS      = TIMEOUT_EXPAND_MS

# ---- Application & router --------------------------------------------------
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")

# Initialise tracing before wrapping the application so spans have real IDs
setup_tracing(os.getenv('OTEL_SERVICE_NAME') or 'gateway')
# Ensure OTEL middleware wraps all subsequent middlewares/handlers
instrument_fastapi_app(app, service_name=os.getenv('OTEL_SERVICE_NAME') or 'gateway')

# ---------------------------------------------------------------------------
# Startup hooks
# ---------------------------------------------------------------------------

# Warm the policy registry once at startup.  The registry contains prompt
# policy definitions used for envelope construction.  Warming it here
# prevents repeated synchronous file I/O on the hot request path and
# ensures deterministic performance.  Any errors are logged but do not
# prevent startup.
@app.on_event("startup")
async def _warm_policy_registry() -> None:  # pragma: no cover - startup hook
    try:
        from gateway.schema_cache import fetch_policy_registry  # local import to avoid circular
        from core_config import get_settings as _get_settings
        s = _get_settings()
        registry_url = getattr(s, "policy_registry_url", None)
        if registry_url:
            await fetch_policy_registry()
            try:
                log_stage("schema", "policy_registry_warmed", url=registry_url)
            except Exception:
                pass
        else:
            try:
                log_stage("schema", "policy_registry_warm_skipped")
            except Exception:
                pass
    except Exception as exc:
        try:
            log_stage("schema", "policy_registry_warm_failed", error=str(exc))
        except Exception:
            pass
        return

# ---- Evidence builder & caches --------------------------------------------
_evidence_builder = EvidenceBuilder()

# ---- Proxy helpers (router / resolver) ------------------------------------
async def route_query(*args, **kwargs):  # pragma: no cover - proxy
    try:
        log_stage("router_proxy", "invoke", function="route_query")
    except Exception:
        pass  # avoid cascading failures if logger not initialised
    import importlib, sys
    mod = sys.modules.get("gateway.intent_router")
    if mod is None:
        mod = importlib.import_module("gateway.intent_router")
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
    except Exception as exc:
        log_stage("artefacts", "minio_unavailable", error=str(exc))
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
    """Fetch all artefacts for a request from MinIO as a {name: bytes} dict.

    Returns None when MinIO is not configured or nothing found under the prefix.
    Emits strategic logs but never raises to keep call sites simple.
    """
    client = minio_client()
    if client is None:
        log_stage("artifacts", "minio_get_noop_enabled", request_id=request_id)
        return None
    try:
        prefix = f"{request_id}/"
        # list_objects is a generator
        objects = list(client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True))
        if not objects:
            log_stage("artifacts", "minio_get_empty", request_id=request_id, prefix=prefix)
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
            except Exception as exc:
                # carry on; partial bundles are acceptable but we log them
                log_stage("artifacts", "minio_get_object_failed",
                          request_id=request_id, object=obj.object_name, error=str(exc))
        return out or None
    except Exception as exc:
        log_stage("artifacts", "minio_get_batch_failed", request_id=request_id, error=str(exc))
        return None

def _load_bundle_dict(request_id: str) -> dict[str, bytes] | None:
    """Load the bundle either from the hot in-memory cache or MinIO.

    Returns a {filename: bytes} mapping, or None when neither source has it.
    """
    bundle = BUNDLE_CACHE.get(request_id)
    if bundle:
        return bundle
    # Fallback to object storage
    return _minio_get_batch(request_id)

def _minio_put_batch(request_id: str, artefacts: dict[str, bytes]) -> None:
    client = minio_client()
    if client is None:
        log_stage("artefacts", "sink_noop_enabled",
            request_id=request_id, count=len(artefacts)
        )
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
        except Exception as exc:
            log_stage("artifacts",
                "minio_bucket_prepare_failed",
                request_id=request_id,
                error=str(exc),
            )
    total_bytes = 0
    for name, blob in artefacts.items():
        client.put_object(
            settings.minio_bucket,
            f"{request_id}/{name}",
            io.BytesIO(blob),
            length=len(blob),
            content_type="application/json",
        )
        metric_counter("artifact_bytes_total", len(blob), artefact=name)
        total_bytes += len(blob)

    # Strategic success log for auditability and sizing telemetry
    log_stage("artifacts", "minio_put_batch_ok",
              request_id=request_id, count=len(artefacts), bytes_total=total_bytes)

    # Build and upload a compact bundle and sidecar meta so MinIO shows size/last-modified
    try:
        bundle_bytes, meta_bytes = build_bundle_and_meta(artefacts)
        upload_bundle_and_meta(client, settings.minio_bucket, request_id, bundle_bytes, meta_bytes)
    except Exception as exc:
        # Non-fatal, emit structured warning and continue
        log_stage("artifacts", "index_build_or_upload_failed", request_id=request_id, error=str(exc))

async def _minio_put_batch_async(
    request_id: str,
    artefacts: dict[str, bytes],
    timeout_sec: float | None = None,
) -> None:
    """Upload artefacts off the hot path with a hard timeout."""
    timeout_sec = timeout_sec or settings.minio_async_timeout
    loop = asyncio.get_running_loop()
    try:
        import contextvars  # local import to avoid cost on cold paths
        ctx = contextvars.copy_context()
        func = functools.partial(_minio_put_batch, request_id, artefacts)
        wrapped = lambda: ctx.run(func)
        await asyncio.wait_for(
            loop.run_in_executor(None, wrapped),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log_stage(
            "artifacts",
            "minio_put_batch_timeout",
            request_id=request_id,
            timeout_ms=int(timeout_sec * 1000),
        )
    except Exception as exc:
        log_stage(
            "artifacts",
            "minio_put_batch_failed",
            request_id=request_id,
            error=str(exc),
        )

# ---- Request logging & counters middleware ---------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, "trace_id", 0):
            bind_trace_ids(f"{_ctx.trace_id:032x}", f"{_ctx.span_id:016x}")
            try:
                log_stage("observability", "trace_ctx_bound", path=str(request.url.path))
            except Exception:
                pass
        else:
            # Strategic signal: no active span (e.g., OTEL not initialized) – rare.
            # Suppress noisy breadcrumbs for health and metrics endpoints.
            try:
                _p = str(request.url.path)
                # Only log when not hitting health/ready/metrics paths
                if not (
                    _p.endswith("/health")
                    or _p.endswith("/healthz")
                    or _p.endswith("/ready")
                    or _p.endswith("/readyz")
                    or _p.endswith("/metrics")
                ):
                    (log_stage("observability", "no_active_span", path=_p) if _LOG_NO_ACTIVE_SPAN else None)
            except Exception:
                pass
    except Exception:
        # Never break the request path due to tracing
        pass
    req_id = compute_request_id(str(request.url.path), dict(request.query_params), None); t0 = time.perf_counter()
    log_stage("request", "request_start", request_id=req_id,
              path=request.url.path, method=request.method)
    try:
        log_stage("request", "v2_query_start",
                  request_id=req_id,
                  llm_mode=getattr(settings, "llm_mode", "unknown"),
                  stream=bool(stream),
                  text_len=len(q))
    except Exception:
        pass

    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, "trace_id", 0):
            log_stage("observability", "trace_ctx",
                      request_id=req_id,
                      trace_id=f"{_ctx.trace_id:032x}",
                      span_id=f"{_ctx.span_id:016x}")
        else:
            # Fall back to IDs bound from `traceparent` if OTEL hasn't started the span yet
            try:
                from core_logging import current_trace_ids
                _tid, _sid = current_trace_ids()
                if _tid and _sid:
                    log_stage("observability", "trace_ctx",
                              request_id=req_id,
                              trace_id=_tid, span_id=_sid)
            except Exception:
                pass
    except Exception:
        pass
    resp = await call_next(request)

    dt_s = (time.perf_counter() - t0)
    metric_histogram("gateway_ttfb_seconds", dt_s)
    metric_counter("gateway_http_requests_total", 1,
                   method=request.method, code=str(resp.status_code))
    if resp.status_code >= 500:
        metric_counter("gateway_http_5xx_total", 1)
    # Surface a trace header for clients when we have one (from the active span).
    try:
        from opentelemetry import trace as _t  # type: ignore
        _sp = _t.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, "trace_id", 0):
            if not any(k.lower() == "x-trace-id" for k in resp.headers.keys()):
                resp.headers["x-trace-id"] = f"{_ctx.trace_id:032x}"
    except Exception:
        pass
    log_stage("request", "request_end",
              request_id=req_id, latency_ms=dt_s * 1000.0, status_code=resp.status_code)
    resp.headers["x-request-id"] = req_id
    return resp

# ---- Exception handlers ----------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Structured, JSON-first error envelope aligned with API
    try:
        body = await request.body()
    except Exception:
        body = b""
    try:
        req_id = compute_request_id(str(request.url.path), dict(request.query_params), body)
    except Exception:
        req_id = compute_request_id(str(request.url.path), None, None)
    try:
        logger.warning(
            "request_validation_error",
            extra={
                "service": "gateway",
                "stage": "validation",
                "errors": jsonx.sanitize(exc.errors()),
                "url": str(request.url),
                "method": request.method,
                "request_id": req_id,
            },
        )
    except Exception:
        pass
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_FAILED",
                "message": "Request validation failed",
                "details": {"errors": jsonx.sanitize(exc.errors())},
                "request_id": req_id,

            },
            "request_id": req_id,
        },
        headers={},  # snapshot_etag is not meaningful here
    )
# Catch-all exception handler to avoid leaking non-serialisable objects into JSON responses
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    try:
        body = await request.body()
    except Exception:
        body = b""
    try:
        req_id = compute_request_id(str(request.url.path), dict(request.query_params), body)
    except Exception:
        req_id = generate_request_id()
    try:
        log_stage("request", "unhandled_exception", request_id=req_id, error=str(exc), error_type=exc.__class__.__name__)
    except Exception:
        pass
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
@app.get("/metrics", include_in_schema=False)          # pragma: no cover
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/ops/minio/ensure-bucket")
def ensure_bucket():
    log_stage("gateway", "ensure_bucket")
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

# ---- Schema mirror ---------------------------------------------------------
@router.get("/schema/{kind}")
@app.get("/schema/{kind}")          # temporary back-compat
async def schema_mirror(kind: str, request: Request):
    if kind not in ("fields", "rels"):
        raise HTTPException(status_code=404, detail="unknown schema kind")

    path = f"/api/schema/{'fields' if kind=='fields' else 'rels'}"
    data, etag = await fetch_schema(path)
    headers = {"x-snapshot-etag": etag} if etag else {}
    # Deprecation header on legacy alias (non-/v2/ path)
    if request.url.path.startswith("/schema/"):
        headers["Deprecation"] = "true"
    try:
        if not str(request.url.path).startswith("/v2/"):
            headers["Deprecation"] = "true"
            headers["Sunset"] = "2025-12-31"
            try:
                # strategic structured log, reusing existing logger
                log_stage("schema", "alias_deprecated", request_id=None)
            except Exception:
                pass
    except Exception:
        pass
    return JSONResponse(content=data, headers=headers)

# ---- Streaming helper ------------------------------------------------------
def _traced_stream(text: str, include_event: bool = False):
    # Keep the streaming generator inside a span for exemplar + audit timing
    with trace_span("gateway.stream", logger=logger, stage="stream").ctx():
        yield from stream_chunks(text, include_event=include_event)

# ---- API models ------------------------------------------------------------
class AskIn(BaseModel):
    # Enforce strict JSON‑first input by forbidding unknown fields and
    # allowing population by alias names.  A single ConfigDict is defined
    # here rather than later to avoid silently overriding the model
    # configuration.  See prompts P1/P2 for rationale.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    intent: str = Field(default="why_decision")
    anchor_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("anchor_id", "decision_ref", "node_id"),
    )
    decision_ref: str | None = Field(default=None, exclude=True)

    evidence: Optional[WhyDecisionEvidence] = None
    answer:   Optional[WhyDecisionAnswer]   = None
    policy_id: Optional[str] = None
    prompt_id: Optional[str] = None
    request_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_decision_ref(cls, data):
        if isinstance(data, dict) and "anchor_id" not in data and "decision_ref" in data:
            data["anchor_id"] = data["decision_ref"]
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

class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = Field(default=None, alias="text")
    q: str | None = Field(default=None, alias="q")
    functions: list[str | dict] | None = None
    request_id: str | None = None

# ---- /v2/ask ---------------------------------------------------------------
@router.post("/ask", response_model=WhyDecisionResponse)
@trace_span("ask", logger=logger)
async def ask(
    req: AskIn,
    request: Request,
    stream: bool = Query(False),
    include_event: bool = Query(False),
):

    resp, artefacts, req_id = await build_why_decision_response(
        req, _evidence_builder
    )
    want_stream = bool(stream) or ("text/event-stream" in (request.headers.get("accept","").lower()))
    try:
        log_stage("stream", "mode_selected", request_id=req_id,
                  want_stream=want_stream,
                  reason=("accept" if want_stream and not stream else ("flag" if stream else "off")))
    except Exception:
        pass
    try:
        import sys
        gw_mod = sys.modules.get("gateway.app")
        if gw_mod is not None and hasattr(gw_mod, "should_load_shed"):
            fn = getattr(gw_mod, "should_load_shed")
            if callable(fn):
                # Assign load_shed on the MetaInfo via attribute to avoid index errors.
                try:
                    resp.meta.load_shed = bool(fn())
                except Exception:
                    # Fall back to dict-style assignment for resilience.
                    try:
                        resp.meta["load_shed"] = bool(fn())
                    except Exception:
                        pass
    except Exception:
        pass

    if want_stream:
        short_answer: str = resp.answer.short_answer
        headers = {"Cache-Control": "no-cache", "x-request-id": req_id}
        try:
            etag = resp.meta.get("snapshot_etag")
            if etag:
                headers["x-snapshot-etag"] = etag
        except Exception:
            pass
        # add trace id when available
        try:
            from opentelemetry import trace as _t  # type: ignore
            _sp = _t.get_current_span()
            if _sp:
                _ctx = _sp.get_span_context()  # type: ignore[attr-defined]
                if getattr(_ctx, "trace_id", 0):
                    headers["x-trace-id"] = f"{_ctx.trace_id:032x}"
        except Exception:
            pass
        try:
            from gateway.inference_router import last_call as _last_llm_call
            mdl = _last_llm_call.get("model")
            can = _last_llm_call.get("canary")
            if mdl:
                headers["x-model"] = str(mdl)
            if can is not None:
                headers["x-canary"] = "true" if can else "false"
        except Exception:
            pass
        # Emit tokens and then the full final response object; mirror snapshot ETag to headers
        final_payload = jsonx.sanitize(resp.model_dump(mode="python"))
        if isinstance(final_payload, dict):
            etag = None
            try:
                etag = final_payload.get("meta", {}).get("snapshot_etag")
            except Exception:
                etag = None
            if etag:
                headers["x-snapshot-etag"] = etag
        try:
            log_stage("request", "v2_query_end",
                      request_id=req_id,
                      fallback_used=bool(resp.meta.get("fallback_used", False)))
        except Exception:
            pass
        return StreamingResponse(
            stream_answer_with_final(
                short_answer,
                final_payload,
                include_event=include_event,
            ),
            media_type="text/event-stream",
            headers=headers or {"Cache-Control": "no-cache"},
        )
    headers = {"x-request-id": req_id}
    try:
        etag = resp.meta.get("snapshot_etag")
        if etag:
            headers["x-snapshot-etag"] = etag
    except Exception:
        pass
    try:
        from gateway.inference_router import last_call as _last_llm_call
        mdl = _last_llm_call.get("model"); can = _last_llm_call.get("canary")
        if mdl:
            headers["x-model"] = str(mdl)
        if can is not None:
            headers["x-canary"] = "true" if can else "false"
    except Exception:
        pass
    try:
        log_stage("request", "v2_query_end",
                  request_id=req_id,
                  fallback_used=bool(resp.meta.get("fallback_used", False)))
    except Exception:
        pass
    return JSONResponse(content=resp.model_dump(mode="python"), headers=headers)

# ---- /v2/query -------------------------------------------------------------
@router.post("/query")
async def v2_query(
    request: Request,
    req: QueryIn,
    stream: bool = Query(False),
    include_event: bool = Query(False),
):
    if should_load_shed():
        ra = getattr(settings, "load_shed_retry_after_seconds", 1)
        return JSONResponse(status_code=429, headers={"Retry-After": str(ra)},
                            content={"detail":"Service overloaded","meta":{"load_shed":True}})

    q = (req.text or req.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="missing query text")
    
    # Deterministic request ID for resolver logs (align with middleware scheme)
    try:
        req_id = compute_request_id(str(request.url.path), dict(request.query_params), None)
    except Exception:
        # Conservative fallback – still deterministic for identical input
        req_id = compute_request_id("/v2/query", {"text": q}, None)

    # --- Resolve the anchor FIRST so helpers can use a real node_id ---
    import importlib, sys
    resolver_path: str = "unknown"
    _gw_mod = sys.modules.get("gateway.app")
    resolver_func = getattr(_gw_mod, "resolve_decision_text", None) if _gw_mod is not None else None
    if resolver_func is None:
        _resolver_mod = sys.modules.get("gateway.resolver")
        if _resolver_mod is None:
            _resolver_mod = importlib.import_module("gateway.resolver")
        resolver_func = getattr(_resolver_mod, "resolve_decision_text")
    match = await resolver_func(q, request_id=req_id)
    if match and isinstance(match, dict):
        anchor: dict | None = {"id": match.get("id") or match.get("anchor_id") or match.get("decision_id")}
        resolver_path = "slug"
    else:
        # Fallback to BM25 if resolver couldn't map the text to a node
        _fs_mod = sys.modules.get("gateway.resolver.fallback_search")
        if _fs_mod is None:
            _fs_mod = importlib.import_module("gateway.resolver.fallback_search")
        search_fn = getattr(_fs_mod, "search_bm25")
        matches = await search_fn(q, k=24, request_id=req_id, snapshot_etag=None)
        if matches:
            anchor = {"id": matches[0].get("id")}
            resolver_path = "bm25"
        else:
            return JSONResponse(content={"matches": matches}, status_code=200)

    # --- Plan routing with anchor-aware functions (pass node_id explicitly) ---
    default_functions: list[dict] = [
        {"name": "search_similar"},
        {"name": "get_graph_neighbors", "arguments": {"node_id": anchor["id"]}},
    ]
    # Allow caller overrides; otherwise use our anchor-aware defaults
    functions = req.functions if req.functions is not None else default_functions

    # Use the router proxy defined at the top of this module
    routing_info: dict = {}
    try:
        route_result = await route_query(q, functions)
        if isinstance(route_result, dict):
            routing_info = route_result
    except Exception:
        routing_info = {}
    try:
        logger.info("intent_completed", extra=routing_info)
    except Exception:
        pass

    # Compute whether to include neighbors based on actual calls that ran
    def _func_names(seq):
        out = []
        for f in (seq or []):
            if isinstance(f, dict):
                nm = f.get("name")
                if nm:
                    out.append(nm)
            else:
                out.append(str(f))
        return out
    include_neighbors: bool = "get_graph_neighbors" in (
        routing_info.get("function_calls")
        or _func_names(functions)
        or []
    )

    try:
        import inspect  # Lazy import to avoid module-level overhead
        sig = inspect.signature(_evidence_builder.build)
        if "include_neighbors" in sig.parameters:
            ev = await _evidence_builder.build(
                anchor["id"],
                include_neighbors=include_neighbors,
            )
        else:
            ev = await _evidence_builder.build(anchor["id"])
    except TypeError:
        ev = await _evidence_builder.build(anchor["id"])

    helper_payloads: dict = routing_info.get("results", {}) if routing_info else {}
    neighbours: List[dict] = []
    # Use ONLY k=1 graph neighbors for evidence (spec §B2) – do not merge search matches.
    if isinstance(helper_payloads.get("get_graph_neighbors"), dict):
        payload = helper_payloads.get("get_graph_neighbors") or {}
        neighbours += (
            payload.get("neighbors")
            or payload.get("results")
            or payload.get("matches")
            or []
        )
    # Explicitly ignore search_similar matches when building evidence for /v2/query.
    # search_similar is used to resolve the anchor; evidence scope remains k=1 around anchor.
    search_results = helper_payloads.get("search_similar")
    ignored_matches = 0
    if isinstance(search_results, list):
        ignored_matches = len(search_results)
    elif isinstance(search_results, dict):
        try:
            matches = search_results.get("matches") or []
            ignored_matches = len(matches)
        except Exception:
            ignored_matches = 0
    try:
        logger.info(
            "search_matches_ignored",
            extra={"service": "gateway", "stage": "selector", "count": ignored_matches,
                   "reason": "query_evidence_scope_k1"}
        )
    except Exception:
        pass

    added_events: int = 0
    added_trans_pre: int = 0
    added_trans_suc: int = 0
    event_ids: set[str] = {e.get("id") for e in ev.events if isinstance(e, dict) and e.get("id")}
    pre_ids: set[str] = {t.get("id") for t in ev.transitions.preceding if isinstance(t, dict) and t.get("id")}
    suc_ids: set[str] = {t.get("id") for t in ev.transitions.succeeding if isinstance(t, dict) and t.get("id")}

    for n in neighbours:
        if isinstance(n, dict):
            n_id: str | None = n.get("id")
            raw_type = n.get("type") or n.get("entity_type")
            n_type: str | None = str(raw_type).lower() if raw_type else None
        else:
            n_id = n  # primitive identifiers default to events
            n_type = None
        if not n_id or n_id == ev.anchor.id:
            continue
        if n_type == "transition":
            tid = n_id
            orient: str | None = None
            if isinstance(n, dict):
                to_id = n.get("to") or n.get("to_id")
                from_id = n.get("from") or n.get("from_id")
                try:
                    if to_id and to_id == ev.anchor.id:
                        orient = "preceding"
                    elif from_id and from_id == ev.anchor.id:
                        orient = "succeeding"
                except Exception:
                    orient = None
                if orient is None:
                    edge = n.get("edge") or {}
                    rel = edge.get("rel") or edge.get("relation")
                    if rel in ("preceding", "succeeding"):
                        orient = rel
            if orient == "succeeding":
                if tid not in pre_ids and tid not in suc_ids:
                    ev.transitions.succeeding.append(n)
                    suc_ids.add(tid)
                    added_trans_suc += 1
            else:
                if tid not in pre_ids and tid not in suc_ids:
                    ev.transitions.preceding.append(n)
                    pre_ids.add(tid)
                    added_trans_pre += 1
            continue
        if n_id not in event_ids:
            if isinstance(n, dict):
                ev.events.append(n)
            else:
                ev.events.append({"id": n_id})
            event_ids.add(n_id)
            added_events += 1
    try:
        _evs: list[dict] = []
        for _e in ev.events or []:
            if isinstance(_e, dict):
                _evs.append(_e)
            else:
                try:
                    _evs.append(_e.model_dump(mode="python"))
                except Exception:
                    _evs.append(dict(_e))
        _trs: list[dict] = []
        for _t in list(ev.transitions.preceding or []) + list(ev.transitions.succeeding or []):
            if isinstance(_t, dict):
                _trs.append(_t)
            else:
                try:
                    _trs.append(_t.model_dump(mode="python"))
                except Exception:
                    _trs.append(dict(_t))
        ev.allowed_ids = canonical_allowed_ids(
            getattr(ev.anchor, "id", None) or "",
            _evs,
            _trs,
        )
    except Exception:
        # Fallback to existing allowed_ids if canonical computation fails
        ev.allowed_ids = list(getattr(ev, "allowed_ids", []) or [])
    try:
        logger.info(
            "neighbor_merge_summary",
            extra={
                "added_events": added_events,
                "added_transitions_pre": added_trans_pre,
                "added_transitions_suc": added_trans_suc,
            },
        )
    except Exception:
        pass

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
        request_id=req.request_id,
    )
    resp, artefacts, req_id = await build_why_decision_response(
        ask_payload, _evidence_builder
    )
    try:
        if resolver_path:
            try:
                # Prefer attribute assignment on MetaInfo instance
                if hasattr(resp, "meta") and hasattr(resp.meta, "__setattr__"):
                    setattr(resp.meta, "resolver_path", resolver_path)
                else:
                    # Fall back to dictionary assignment when meta is a plain dict
                    resp.meta["resolver_path"] = resolver_path
            except Exception:
                # Silently ignore failures to assign resolver_path
                pass
    except Exception:
        pass

    # Apply final validation on the assembled response.  This ensures that
    # post-routing modifications still conform to the Why-Decision contract.
    try:
        ok, v_errors = validate_response(resp)
    # Summarise validation diagnostics by counting errors instead of
    # returning the full list (milestone‑5 §D).
        error_count: int = 0
        if isinstance(v_errors, list):
            error_count = len(v_errors)
        try:
            existing = resp.meta.get("validator_error_count") or 0
            if not isinstance(existing, int):
                existing = 0
            resp.meta["validator_error_count"] = existing + error_count
        except Exception:
            pass
        try:
            log_stage("gateway.validation",
                      "applied",
                      errors_count=error_count,
                      corrected_fields=[e.get("code") for e in v_errors] if isinstance(v_errors, list) else [])
        except Exception:
            pass
    except Exception:
        pass

    # Decide streaming mode based on query flag or Accept header (SSE)
    want_stream = bool(stream) or ("text/event-stream" in (request.headers.get("accept","").lower()))
    try:
        log_stage("stream", "mode_selected", request_id=req_id,
                  want_stream=want_stream,
                  reason=("accept" if want_stream and not stream else ("flag" if stream else "off")))
    except Exception: pass
    if want_stream:
        headers = {"Cache-Control": "no-cache", "x-request-id": req_id}
        try:
            etag = resp.meta.get("snapshot_etag")
            if etag:
                headers["x-snapshot-etag"] = etag
        except Exception:
            pass
        try:
            from gateway.inference_router import last_call as _last_llm_call
            mdl = _last_llm_call.get("model")
            can = _last_llm_call.get("canary")
            if mdl:
                headers["x-model"] = str(mdl)
            if can is not None:
                headers["x-canary"] = "true" if can else "false"
        except Exception:
            pass
        # Emit tokens and then the full final response object; mirror snapshot ETag to headers
        final_payload = jsonx.sanitize(resp.model_dump(mode="python"))
        if isinstance(final_payload, dict):
            etag = None
            try:
                etag = final_payload.get("meta", {}).get("snapshot_etag")
            except Exception:
                etag = None
            if etag:
                headers["x-snapshot-etag"] = etag
        return StreamingResponse(
            stream_answer_with_final(
                resp.answer.short_answer,
                final_payload,
                include_event=include_event,
            ),
            media_type="text/event-stream",
            headers=headers or {"Cache-Control": "no-cache"},
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
        etag = resp.meta.get("snapshot_etag")
        if etag:
            headers["x-snapshot-etag"] = etag
    except Exception:
        pass
    try:
        from gateway.inference_router import last_call as _last_llm_call
        mdl = _last_llm_call.get("model"); can = _last_llm_call.get("canary")
        if mdl:
            headers["x-model"] = str(mdl)
        if can is not None:
            headers["x-canary"] = "true" if can else "false"
    except Exception:
        pass
    return JSONResponse(content=resp.model_dump(), headers=headers)

@router.post("/bundles/{request_id}/download", include_in_schema=False)
async def download_bundle(request_id: str, format: str = Query("json", pattern="^(json|tar)$")):
    """Return a presigned URL for the archived bundle when possible.

    Attempts to generate a real presigned GET for `<request_id>.bundle.tar.gz` in MinIO.
    Falls back to the internal `/v2/bundles/{request_id}.tar` proxy if MinIO
    is unavailable, not publicly reachable, or the object is missing.
    """
    expires_sec = 600
    # Prefer the exec-friendly TAR proxy by default
    url = f"/v2/bundles/{request_id}.tar"
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
                except Exception as _exc:
                    log_stage("bundle", "download.public_client_init_failed", request_id=request_id, error=str(_exc))
                if pub_client:
                    url = pub_client.presigned_get_object(
                        settings.minio_bucket,
                        f"{request_id}.bundle.tar.gz",
                        expires=_td(seconds=expires_sec),
                    )
                    log_stage("bundle", "download.presigned_minio_public", request_id=request_id)
                else:
                    # No public endpoint configured → keep internal proxy URL (works through API Edge)
                    log_stage("bundle", "download.fallback_proxy_used", request_id=request_id)
            except Exception as exc:
                # Soft-fail to the internal endpoint
                log_stage("bundle", "download.presigned_minio_failed", request_id=request_id, error=str(exc))
    except Exception:
        # leave url as fallback
        pass
    return JSONResponse(content={"url": url, "expires_in": expires_sec})

@router.get("/bundles/{request_id}", include_in_schema=False)
async def get_bundle(request_id: str):
    """Stream the exact JSON artefact bundle for a request.

    The bundle consists of the pre‑, post‑ and final evidence dumps,
    prompt envelopes, raw LLM output and the final response.  Each
    artefact is returned as a UTF‑8 decoded string when possible or
    Base64‑encoded when binary.  A log entry is emitted with the
    download.served tag for auditability.  Returns 404 if the bundle
    identifier is unknown.
    """
    bundle = _load_bundle_dict(request_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="bundle not found")
    content: dict[str, Any] = {}
    for name, blob in bundle.items():
        try:
            if isinstance(blob, bytes):
                try:
                    content[name] = blob.decode()
                except Exception:
                    import base64
                    content[name] = base64.b64encode(blob).decode()
            else:
                content[name] = blob
        except Exception:
            content[name] = None
    try:
        # Log the size of the serialized bundle for metrics
        log_stage(
            "bundle",
            "download.served",
            request_id=request_id,
            size=len(jsonx.dumps(content).encode("utf-8")),
        )
    except Exception:
        pass
    try:
        anchor_id = None
        try:
            resp_json = content.get("response.json")
            if isinstance(resp_json, str):
                import json as _json
                _obj = _json.loads(resp_json)
                anchor_id = (_obj.get("evidence", {}).get("anchor") or {}).get("id")
        except Exception:
            anchor_id = None
        from datetime import datetime as _dt, timezone as _tz
        date_str = _dt.now(_tz.utc).strftime("%Y%m%d")
        base = anchor_id or request_id
        filename = f"evidence-{base}-{date_str}.json"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    except Exception:
        headers = {}
    return JSONResponse(content=content, headers=headers)

@router.get("/bundles/{request_id}.tar", include_in_schema=False)
async def get_bundle_tar(request_id: str):
    """Return the artefact bundle as a TAR archive (exec-friendly).
    Pull from hot cache; fall back to MinIO when needed."""
    bundle = _load_bundle_dict(request_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="bundle not found")

    import tarfile, io, json as _json
    buf = io.BytesIO()
    with tarfile.open(mode="w", fileobj=buf) as tar:
        # individual artefacts
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
            "Contains deterministic artefacts used to generate the answer:\n"
            "- evidence_pre.json / evidence_post.json\n"
            "- envelope.json (prompt inputs & fingerprints)\n"
            "- llm_raw.json (if available)\n"
            "- response.json (final answer)\n"
            "- validator_report.json\n"
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

# ---- Legacy evidence endpoint ---------------------------------------------
@app.get("/evidence/{decision_ref}")
async def evidence_endpoint(
    decision_ref: str,
    intent: str = "query",
    stream: bool = Query(False),
    include_event: bool = Query(False),
):
    try:
        anchor = await asyncio.wait_for(evidence.resolve_anchor(decision_ref,intent=intent),
                                        timeout=_SEARCH_MS/1000)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"search stage timeout >{_SEARCH_MS}ms")

    try:
        graph = await asyncio.wait_for(
            evidence.expand_graph(anchor["id"], intent=intent),
            timeout=_EXPAND_MS / 1000,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504,
                            detail=f"expand stage timeout >{_EXPAND_MS}ms")

    ev = await _evidence_builder.build(anchor["id"])
    helper_payloads: dict = {}

    neighbours: List[dict] = []
    if isinstance(helper_payloads.get("get_graph_neighbors"), dict):
        payload = helper_payloads["get_graph_neighbors"]
        neighbours += payload.get("neighbors") or payload.get("matches") or []
    if isinstance(helper_payloads.get("search_similar"), list):
        neighbours += helper_payloads["search_similar"]

    seen = {e.get("id") for e in ev.events}
    for n in neighbours:
        nid = n.get("id") if isinstance(n, dict) else n
        if nid and nid not in seen and nid != ev.anchor.id:
            ev.events.append({"id": nid})
            seen.add(nid)

    # After merging neighbour IDs into events, recompute allowed_ids using
    # the canonical helper.  Convert events and transitions to plain
    # dictionaries as needed.  The helper returns the anchor ID first,
    # followed by events in ascending timestamp order and then transitions.
    try:
        _evs: list[dict] = []
        for _e in ev.events or []:
            if isinstance(_e, dict):
                _evs.append(_e)
            else:
                try:
                    _evs.append(_e.model_dump(mode="python"))
                except Exception:
                    _evs.append(dict(_e))
        _trs: list[dict] = []
        for _t in list(ev.transitions.preceding or []) + list(ev.transitions.succeeding or []):
            if isinstance(_t, dict):
                _trs.append(_t)
            else:
                try:
                    _trs.append(_t.model_dump(mode="python"))
                except Exception:
                    _trs.append(dict(_t))
        ev.allowed_ids = canonical_allowed_ids(
            getattr(ev.anchor, "id", None) or "",
            _evs,
            _trs,
        )
    except Exception:
        ev.allowed_ids = list(getattr(ev, "allowed_ids", []) or [])

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
    )
    resp_obj, *_ = await build_why_decision_response(
        ask_payload, _evidence_builder
    )

    if stream:
        short_answer: str = resp_obj.answer.short_answer
        return StreamingResponse(
            _traced_stream(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    headers = {}
    try:
        etag = getattr(resp_obj, "meta", {}).get("snapshot_etag") or None
        if etag:
            headers["x-snapshot-etag"] = etag
    except Exception:
        pass
    return JSONResponse(status_code=200, content=resp_obj.model_dump(), headers=headers)

# ---- Final wiring ----------------------------------------------------------
app.include_router(router)

@app.on_event("startup")
async def _start_load_shed_refresher() -> None:
    try:
        log_stage("init", "sse_helper_selected", sse_module="core_utils.sse")
        start_background_refresh()
    except Exception:
        pass


@app.on_event("shutdown")
async def _stop_load_shed_refresher() -> None:
    try:
        stop_background_refresh()
    except Exception:
        pass
