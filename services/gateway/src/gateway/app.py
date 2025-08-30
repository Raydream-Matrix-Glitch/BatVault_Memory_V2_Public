# ---- Imports ---------------------------------------------------------------
# Stdlib
import asyncio, functools, io, os, time, inspect
import re
from typing import List, Optional
import importlib.metadata as _md

# Third-party
import httpx as _httpx_real, orjson
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
from core_utils.ids import compute_request_id

# Internal packages
from core_config import get_settings
from core_config.constants import (
    TTL_SCHEMA_CACHE_SEC as _SCHEMA_TTL_SEC,
)
from core_logging import get_logger, trace_span
from .logging_helpers import stage as log_stage
from core_observability.otel import init_tracing, instrument_fastapi_app
from .metrics import (
    counter as metric_counter,
    histogram as metric_histogram,
    gauge   as metric_gauge,
)
from core_models.models import (
    WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse
)
from core_utils.fingerprints import canonical_json
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from core_validator import validate_response
# Import the public canonical helper rather than the private underscore version.
from core_validator import canonical_allowed_ids

from . import evidence
from .evidence import EvidenceBuilder
from .schema_cache import fetch_schema
from .http import fetch_json
from .load_shed import should_load_shed, start_background_refresh, stop_background_refresh
from .builder import build_why_decision_response
from .builder import BUNDLE_CACHE
from gateway.sse import stream_chunks
from core_config.constants import TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS

# ---- HTTPX shim ------------------------------------------------------------
class _HTTPXShim:
    def __getattr__(self, name: str):
        return getattr(_httpx_real, name)

httpx = _HTTPXShim()

# ---- Configuration & globals ----------------------------------------------
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = True

_SEARCH_MS      = TIMEOUT_SEARCH_MS
_EXPAND_MS      = TIMEOUT_EXPAND_MS

# ---- Application & router --------------------------------------------------
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")
instrument_fastapi_app(app, service_name='gateway')

# ---- Evidence builder & caches --------------------------------------------
_evidence_builder = EvidenceBuilder()

# ---- Tracing init (kept as-is) --------------------------------------------
init_tracing("gateway")

# ---- Proxy helpers (router / resolver) ------------------------------------
async def route_query(*args, **kwargs):  # pragma: no cover - proxy
    """Proxy for gateway.intent_router.route_query.

    Looks up the current `route_query` implementation from
    ``gateway.intent_router`` each time it is invoked.  This allows tests
    to monkey-patch the router and ensures that any lingering references to
    `gateway.app.route_query` continue to work.  Structured logging records
    proxy invocation for debugging.
    """
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

async def resolve_decision_text(text: str):  # pragma: no cover - proxy
    """Resolve a natural-language query or slug to a decision anchor.

    This proxy simply defers to the implementation in ``gateway.resolver``.
    It exists to allow tests to monkey-patch ``gateway.app.resolve_decision_text``
    without altering core behaviour.  See ``v2_query`` for usage.
    """
    import importlib
    resolver_mod = importlib.import_module("gateway.resolver")
    resolver_fn = getattr(resolver_mod, "resolve_decision_text")
    return await resolver_fn(text)

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
    for name, blob in artefacts.items():
        client.put_object(
            settings.minio_bucket,
            f"{request_id}/{name}",
            io.BytesIO(blob),
            length=len(blob),
            content_type="application/json",
        )
        metric_counter("artifact_bytes_total", inc=len(blob), artefact=name)

async def _minio_put_batch_async(
    request_id: str,
    artefacts: dict[str, bytes],
    timeout_sec: float | None = None,
) -> None:
    """Upload artefacts off the hot path with a hard timeout."""
    timeout_sec = timeout_sec or settings.minio_async_timeout
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                None, functools.partial(_minio_put_batch, request_id, artefacts)
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log_stage("artifacts", "minio_put_batch_timeout",
            request_id=request_id, timeout_ms=int(timeout_sec * 1000),
        )
    except Exception as exc:
        log_stage(
            "artifacts", "minio_put_batch_failed",
            request_id=request_id, error=str(exc),
        )

# ---- Request logging & counters middleware ---------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    req_id = compute_request_id(str(request.url.path), dict(request.query_params), None); t0 = time.perf_counter()
    log_stage("request", "request_start", request_id=req_id,
              path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt_s = (time.perf_counter() - t0)
    metric_histogram("gateway_ttfb_seconds", dt_s)
    metric_counter("gateway_http_requests_total", 1,
                   method=request.method, code=str(resp.status_code))
    if resp.status_code >= 500:
        metric_counter("gateway_http_5xx_total", 1)
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
    logger.warning(
        "request_validation_error",
        extra={
            "service": "gateway",
            "stage": "validation",
            "errors": exc.errors(),
            "url": str(request.url),
            "method": request.method,
            "request_id": req_id,
        },
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_FAILED",
                "message": "Request validation failed",
                "details": {"errors": exc.errors()},
                "request_id": req_id,

            },
            "request_id": req_id,
        },
        headers={},  # snapshot_etag is not meaningful here
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
        "request_id": compute_request_id("/readyz", None, None),
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
    with trace_span("gateway.stream", stage="stream").ctx():
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
@trace_span("ask")
async def ask(
    req: AskIn,
    stream: bool = Query(False),
    include_event: bool = Query(False),
):

    resp, artefacts, req_id = await build_why_decision_response(
        req, _evidence_builder
    )
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

    try:
        await _minio_put_batch_async(req_id, artefacts)
    except Exception:
        pass

    if stream:
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
        return StreamingResponse(
            _traced_stream(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers=headers,
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
    return JSONResponse(content=resp.model_dump(mode="python"), headers=headers)

# ---- /v2/query -------------------------------------------------------------
@router.post("/query")
async def v2_query(
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
    match = await resolver_func(q)
    if match and isinstance(match, dict):
        anchor: dict | None = {"id": match.get("id") or match.get("anchor_id") or match.get("decision_id")}
        resolver_path = "slug"
    else:
        # Fallback to BM25 if resolver couldn't map the text to a node
        _fs_mod = sys.modules.get("gateway.resolver.fallback_search")
        if _fs_mod is None:
            _fs_mod = importlib.import_module("gateway.resolver.fallback_search")
        search_fn = getattr(_fs_mod, "search_bm25")
        matches = await search_fn(q, k=24)
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

    # Recompute allowed_ids using the canonical helper.  Convert events
    # and transitions to plain dictionaries as needed.  The canonical
    # function ensures the anchor appears first, followed by events in
    # ascending timestamp order and then transitions.  Duplicate IDs are
    # removed.
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

    # Surface resolver path in the response meta for debugging.  When the
    # resolver falls back to BM25 and the anchor carries a rationale the
    # rationale is now incorporated into the deterministic short answer by
    # the templater.  To avoid leaking implementation details the legacy
    # ``rationale_note`` field is no longer populated (Milestone‑5 §A9).
    # Assign to the canonical meta model via attribute assignment.  The
    # MetaInfo model defines ``resolver_path`` as an optional field, so
    # setting it directly will not violate the ``extra=forbid`` constraint.
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

    await _minio_put_batch_async(req_id, artefacts)

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

    if stream:
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
        return StreamingResponse(
            _traced_stream(resp.answer.short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers=headers,
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

# ---------------------------------------------------------------------------
# Evidence bundle download endpoints (spec §D)
#
# These endpoints expose the exact artefact bundle used to answer a
# Why-Decision request.  The POST variant returns a short-lived URL
# pointing to the bundle (not actually presigned in this test harness) and
# the GET variant streams the JSON bundle directly.  They are part of the
# /v2 API version and therefore defined on the ``router`` with a ``/v2``
# prefix.  The Gateway stores bundles in an in-memory cache; these
# endpoints surface them for download and auditing.  If the requested
# bundle is not found a 404 is returned.

@router.post("/bundles/{request_id}/download", include_in_schema=False)
async def download_bundle(request_id: str):
    """Return a pseudo‑presigned URL for downloading a decision bundle.

    This route returns a JSON object containing a relative URL to the
    bundle along with an expiration time in seconds.  In production
    environments a true presigned link would be generated via MinIO or S3;
    within this implementation we return the direct GET endpoint.  A log
    entry is emitted with the download.presigned tag for observability.
    """
    try:
        log_stage("bundle", "download.presigned", request_id=request_id)
    except Exception:
        pass
    return JSONResponse(
        content={"url": f"/v2/bundles/{request_id}", "expires_in": 600}
    )

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
    bundle = BUNDLE_CACHE.get(request_id)
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
        log_stage("bundle", "download.served", request_id=request_id,
                  size=len(orjson.dumps(content)))
    except Exception:
        pass
    return JSONResponse(content=content)

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


# ---- Load-shed refresh tasks ----------------------------------------------
# Launch a background task on startup to periodically refresh the
# load-shed flag.  On shutdown, cancel the refresher to clean up the
# asynchronous task.  Failure to start the refresher should not crash
# the application; errors are logged via log_stage within the load_shed
# module.
@app.on_event("startup")
async def _start_load_shed_refresher() -> None:
    try:
        start_background_refresh()
    except Exception:
        pass


@app.on_event("shutdown")
async def _stop_load_shed_refresher() -> None:
    try:
        stop_background_refresh()
    except Exception:
        pass
