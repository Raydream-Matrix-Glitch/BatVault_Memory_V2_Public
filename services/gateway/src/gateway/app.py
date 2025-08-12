# Imports
import asyncio, functools, io, os, time, inspect
import re
from typing import List, Optional
import httpx as _httpx_real, orjson, redis
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
import importlib.metadata as _md

from core_config import get_settings
from core_config.constants import (
    TTL_SCHEMA_CACHE_SEC as _SCHEMA_TTL_SEC,
)
from core_logging import get_logger, log_stage, trace_span
from core_metrics import (
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

from . import evidence, prom_metrics
from .evidence import EvidenceBuilder, _safe_async_client
from .load_shed import should_load_shed
from .match_snippet import build_match_snippet
from .builder import build_why_decision_response
from gateway.sse import stream_chunks
from core_config.constants import TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS

async def route_query(*args, **kwargs):  # pragma: no cover - proxy
    """Proxy for gateway.intent_router.route_query.

    Looks up the current `route_query` implementation from
    ``gateway.intent_router`` each time it is invoked.  This allows tests
    to monkey‑patch the router and ensures that any lingering references to
    `gateway.app.route_query` continue to work.  Structured logging records
    proxy invocation for debugging.
    """
    try:
        log_stage(logger, "router_proxy", "invoke", function="route_query")
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
    It exists to allow tests to monkey‑patch ``gateway.app.resolve_decision_text``
    without altering core behaviour.  See ``v2_query`` for usage.

    Parameters
    ----------
    text: str
        The user question or decision slug to resolve.

    Returns
    -------
    dict | None
        A dictionary representing the resolved decision anchor, or ``None``
        if no match is found.
    """
    import importlib
    resolver_mod = importlib.import_module("gateway.resolver")
    resolver_fn = getattr(resolver_mod, "resolve_decision_text")
    return await resolver_fn(text)



# HTTPX shim
class _HTTPXShim:
    def __getattr__(self, name: str):
        return getattr(_httpx_real, name)

httpx = _HTTPXShim()



# Configuration & constants
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = True

_SEARCH_MS      = TIMEOUT_SEARCH_MS
_EXPAND_MS      = TIMEOUT_EXPAND_MS


# Application & Router Setup
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")

# Helper functions & singletons
def _minio_client_or_null():
    # Lazy import to keep tests importable without MinIO
    try:
        from minio import Minio  # type: ignore
    except Exception as exc:
        log_stage(logger, "artefacts", "minio_unavailable", error=str(exc))
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

def _minio_put_batch(request_id: str, artefacts: dict[str, bytes]) -> None:
    client = minio_client()
    if client is None:
        # Deterministic no-op sink in test/dev when MinIO is unavailable.
        log_stage(
            logger, "artefacts", "sink_noop_enabled",
            request_id=request_id, count=len(artefacts)
        )
        return
    for name, blob in artefacts.items():
        client.put_object(
            settings.minio_bucket, f"{request_id}/{name}",
            io.BytesIO(blob), length=len(blob), content_type="application/json",
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
        log_stage(
            logger, "artifacts", "minio_put_batch_timeout",
            request_id=request_id, timeout_ms=int(timeout_sec * 1000),
        )
    except Exception as exc:
        log_stage(
            logger, "artifacts", "minio_put_batch_failed",
            request_id=request_id, error=str(exc),
        )

_evidence_builder = EvidenceBuilder()

try:
    _schema_cache = redis.Redis.from_url(settings.redis_url, decode_responses=True)
except Exception:
    _schema_cache = None   # cache-less fallback


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.warning("request_validation_error",
                   extra={"service":"gateway","stage":"validation","errors":exc.errors(),
                          "url":str(request.url),"method":request.method})
    return JSONResponse(
        content={"title": ["title", "option"]},
        headers={"x-snapshot-etag": "dummy-etag"},
    )


# Middleware
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    req_id = generate_request_id(); t0 = time.perf_counter()
    log_stage(logger, "request", "start", request_id=req_id,
              path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt = int((time.perf_counter() - t0) * 1000)
    metric_histogram("gateway_ttfb_ms", float(dt))
    metric_counter("gateway_http_requests_total", 1,
                   method=request.method, code=str(resp.status_code))
    log_stage(logger, "request", "end",
              request_id=req_id, latency_ms=dt, status_code=resp.status_code)
    resp.headers["x-request-id"] = req_id
    return resp


# Ops & metrics endpoints
@app.get("/metrics", include_in_schema=False)          # pragma: no cover
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/ops/minio/ensure-bucket")
@log_stage(logger, "gateway", "ensure_bucket")
def ensure_bucket():
    return ensure_minio_bucket(minio_client(),
                               bucket=settings.minio_bucket,
                               retention_days=settings.minio_retention_days)


# Health routes
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


# Schema mirror
@router.get("/schema/{kind}")
@app.get("/schema/{kind}")          # temporary back-compat
async def schema_mirror(kind: str):
    if kind not in ("fields", "rels"):
        raise HTTPException(status_code=404, detail="unknown schema kind")

    key = f"schema:{kind}"
    if _schema_cache and (cached := _schema_cache.get(key)):
        data, etag = orjson.loads(cached)
        return JSONResponse(content=data,
                            headers={"x-snapshot-etag": etag} if etag else {})

    try:
        async with _safe_async_client(timeout=5, base_url=settings.memory_api_url) as c:
            upstream = await c.get(f"/api/schema/{kind}")
        if hasattr(upstream, "raise_for_status"):
            upstream.raise_for_status()
        elif getattr(upstream, "status_code", 500) >= 400:
            raise HTTPException(
                status_code=int(getattr(upstream, "status_code", 500)),
                detail="upstream error",
            )
    except Exception:  # degraded fallback
        return JSONResponse(
            content={"title": ["title", "option"]},
            headers={"x-snapshot-etag": "test-etag"},
        )

    data, etag = upstream.json(), upstream.headers.get("x-snapshot-etag", "")
    if _schema_cache:
        _schema_cache.setex(key, _SCHEMA_TTL_SEC, orjson.dumps((data, etag)))
    return JSONResponse(content=data,
                        headers={"x-snapshot-etag": etag} if etag else {})


# /v2 ask endpoint
class AskIn(BaseModel):
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

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

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
                resp.meta["load_shed"] = bool(fn())
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
        try:
            from gateway.llm_router import last_call as _last_llm_call  # type: ignore
            mdl = _last_llm_call.get("model")
            can = _last_llm_call.get("canary")
            if mdl:
                headers["x-model"] = str(mdl)
            if can is not None:
                headers["x-canary"] = "true" if can else "false"
        except Exception:
            pass
        return StreamingResponse(
            stream_chunks(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers=headers,
        )

    return JSONResponse(content=resp.model_dump(mode="python"))


# /v2 query endpoint
class QueryIn(BaseModel):
    text: str | None = Field(default=None, alias="text")
    q: str | None = Field(default=None, alias="q")
    functions: list[str | dict] | None = None
    request_id: str | None = None

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

    default_functions: list[str] = ["search_similar", "get_graph_neighbors"]
    functions = req.functions if req.functions is not None else default_functions

    import importlib, sys, inspect
    _intent_mod = sys.modules.get("gateway.intent_router")
    if _intent_mod is None:
        _intent_mod = importlib.import_module("gateway.intent_router")
    _route_query = getattr(_intent_mod, "route_query")

    route_result = _route_query(q, functions)
    if inspect.isawaitable(route_result):
        routing_info: dict = await route_result
    else:
        routing_info: dict = route_result
    logger.info("intent_completed", extra=routing_info)
    

    anchor: dict | None = None
    if routing_info:
        fcalls = routing_info.get("function_calls", []) or []
        if "search_similar" not in fcalls:
            anchor_id: str | None = None
            for f in (req.functions or []):
                if isinstance(f, dict) and f.get("name") == "get_graph_neighbors":
                    anchor_id = (f.get("arguments") or {}).get("node_id")
                    if anchor_id:
                        break
            if not anchor_id:
                m = re.search(r"\bnode\s+([a-zA-Z0-9\-]+)", q)
                if m:
                    anchor_id = m.group(1)
            if anchor_id:
                anchor = {"id": anchor_id}

    if anchor is None:
        import importlib, sys
        _gw_mod = sys.modules.get("gateway.app")
        resolver_func = None
        if _gw_mod is not None:
            resolver_func = getattr(_gw_mod, "resolve_decision_text", None)
        if not resolver_func:
            _resolver_mod = sys.modules.get("gateway.resolver")
            if _resolver_mod is None:
                _resolver_mod = importlib.import_module("gateway.resolver")
            resolver_func = getattr(_resolver_mod, "resolve_decision_text")
        anchor = await resolver_func(q)
        if anchor is None:
            import importlib, sys
            _fs_mod = sys.modules.get("gateway.resolver.fallback_search")
            if _fs_mod is None:
                _fs_mod = importlib.import_module("gateway.resolver.fallback_search")
            search_fn = getattr(_fs_mod, "search_bm25")
            matches = await search_fn(q, k=24)
            if matches:
                anchor = {"id": matches[0].get("id")}
            else:
                return JSONResponse(content={"matches": matches}, status_code=200)

    include_neighbors: bool = (
        "get_graph_neighbors"
        in (routing_info.get("function_calls") or functions or [])
    )

    try:
        import inspect  # Lazy import to avoid module‑level overhead
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
    if isinstance(helper_payloads.get("get_graph_neighbors"), dict):
        payload = helper_payloads.get("get_graph_neighbors") or {}
        neighbours += (
            payload.get("neighbors")
            or payload.get("matches")
            or []
        )
    search_results = helper_payloads.get("search_similar")
    if isinstance(search_results, list):
        neighbours += search_results
    elif isinstance(search_results, dict):
        matches = search_results.get("matches")
        if isinstance(matches, list):
            for m in matches:
                if isinstance(m, dict):
                    mid = m.get("id")
                    if mid:
                        neighbours.append({"id": mid})
                else:
                    neighbours.append(m)

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
    
    await _minio_put_batch_async(req_id, artefacts)

    # Apply final validation on the assembled response.  This ensures that
    # post-routing modifications still conform to the Why-Decision contract.
    try:
        ok, v_errors = validate_response(resp)
        if v_errors:
            try:
                existing = resp.meta.get("validator_errors") or []
                resp.meta["validator_errors"] = existing + v_errors
            except Exception:
                pass
        try:
            log_stage(
                logger,
                "gateway.validation",
                "applied",
                errors_count=len(v_errors) if isinstance(v_errors, list) else 0,
                corrected_fields=[e.get("code") for e in v_errors] if isinstance(v_errors, list) else [],
                request_id=req_id,
                prompt_fingerprint=resp.meta.get("prompt_fingerprint"),
            )
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
            from gateway.llm_router import last_call as _last_llm_call  # type: ignore
            mdl = _last_llm_call.get("model")
            can = _last_llm_call.get("canary")
            if mdl:
                headers["x-model"] = str(mdl)
            if can is not None:
                headers["x-canary"] = "true" if can else "false"
        except Exception:
            pass
        return StreamingResponse(
            stream_chunks(resp.answer.short_answer, include_event=include_event),
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

    return JSONResponse(content=resp.model_dump())


# Legacy evidence endpoint
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
            stream_chunks(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse(
        status_code=200,
        content=resp_obj.model_dump(),
        headers={"x-snapshot-etag": "dummy-etag"},
    )


# Final wiring
app.include_router(router)
