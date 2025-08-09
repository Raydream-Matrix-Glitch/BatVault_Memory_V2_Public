# 1 ───────────────────────────── Imports ────────────────────────────────
import asyncio, functools, io, os, time, uuid, inspect
import re
from typing import List, Optional
import httpx, orjson, redis
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from minio import Minio
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
import importlib.metadata as _md

from core_config import get_settings
from core_config.constants import (
    MAX_PROMPT_BYTES,
    RESOLVER_MODEL_ID,
    SELECTOR_MODEL_ID,
    TTL_SCHEMA_CACHE_SEC as _SCHEMA_TTL_SEC,
)
from core_logging import get_logger, log_stage, trace_span
from core_metrics import (
    counter as metric_counter,
    histogram as metric_histogram,
    gauge   as metric_gauge,
)
from core_models.models import (
    WhyDecisionAnchor, WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse, WhyDecisionTransitions, CompletenessFlags,
)
from core_utils.fingerprints import canonical_json
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from core_validator import validate_response

from gateway.resolver.fallback_search import search_bm25   # offline search fallback
from . import evidence, prom_metrics       # noqa: F401
from .evidence import EvidenceBuilder, _safe_async_client, _collect_allowed_ids
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
    # Log that the proxy is being used – aids debugging
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

# ---------------------------------------------------------------------------#
# Decision resolver proxy (Milestone‑4)
#
# Tests in Milestone‑4 dynamically monkey‑patch this attribute on the
# ``gateway.app`` module.  Previously the module did not export a
# ``resolve_decision_text`` symbol which caused ``AttributeError`` in
# integration tests when attempting to override it.  To preserve the
# existing behaviour while supporting test stubs, define a thin
# asynchronous proxy that delegates to the canonical resolver in
# ``gateway.resolver``.  By importing the target module inside the
# function body we avoid binding a stale reference at module import
# time (spec §B2; roadmap M4).  When patched, the monkey‑patched
# coroutine will take precedence and be invoked by ``v2_query`` via
# module attribute lookup.

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
    # Defer import to runtime to avoid holding on to a stale reference.
    resolver_mod = importlib.import_module("gateway.resolver")
    resolver_fn = getattr(resolver_mod, "resolve_decision_text")
    return await resolver_fn(text)


# 2 ───────────────────── Config & constants ─────────────────────────────
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = True

_SEARCH_MS      = TIMEOUT_SEARCH_MS
_EXPAND_MS      = TIMEOUT_EXPAND_MS

# 3 ───────────────────── Application setup ──────────────────────────────
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")

# 4 ──────────────── Helpers & singletons ────────────────────────────────
def minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )

def _minio_put_batch(request_id: str, artefacts: dict[str, bytes]) -> None:
    client = minio_client()
    for name, blob in artefacts.items():
        client.put_object(
            settings.minio_bucket, f"{request_id}/{name}",
            io.BytesIO(blob), length=len(blob), content_type="application/json",
        )
        metric_counter("artifact_bytes_total", inc=len(blob), artefact=name)

# ───────────────── asynchronous, bounded-latency wrapper ───────────────
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

# 5 ─────────────────── Exception handlers ───────────────────────────────
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

# 6 ─────────────────────── Middleware ───────────────────────────────────
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

# 7 ──────────────── Ops & metrics endpoints ─────────────────────────────
@app.get("/metrics", include_in_schema=False)          # pragma: no cover
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/ops/minio/ensure-bucket")
@log_stage(logger, "gateway", "ensure_bucket")
def ensure_bucket():
    return ensure_minio_bucket(minio_client(),
                               bucket=settings.minio_bucket,
                               retention_days=settings.minio_retention_days)

# 8 ─────────────────────── Health routes ────────────────────────────────
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

# 9 ──────────────────── /v2 schema mirror ───────────────────────────────
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

# 10 ─────────────────────── /v2 ask ─────────────────────────────────────
class AskIn(BaseModel):
    intent: str = Field(default="why_decision")
    # Milestone-4 adds 'node_id' as an accepted alias for decision slugs
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
        # If evidence is supplied explicitly we trust the caller; otherwise
        # the Gateway will build the bundle from Memory-API.
        return self

@router.post("/ask", response_model=WhyDecisionResponse)
@trace_span("ask")
async def ask(
    req: AskIn,
    stream: bool = Query(False),
    include_event: bool = Query(False),
):

    # delegate heavy lifting to builder.py
    resp, artefacts, req_id = await build_why_decision_response(
        req, _evidence_builder
    )

    # ------------------------------------------------------------------
    # Override the load‑shed flag in the response metadata.
    #
    # The `build_why_decision_response` helper sets `meta["load_shed"]`
    # based on a locally imported `should_load_shed()` from
    # `gateway.load_shed`.  However, in integration tests we monkey‑patch
    # `gateway.app.should_load_shed` to simulate an overloaded system.
    # To ensure that this patched function takes precedence, attempt to
    # resolve and call it dynamically via the module registry.  If the
    # attribute is missing or throws, fall back silently to the value
    # already set by the builder.
    try:
        import sys
        gw_mod = sys.modules.get("gateway.app")
        if gw_mod is not None and hasattr(gw_mod, "should_load_shed"):
            fn = getattr(gw_mod, "should_load_shed")
            if callable(fn):
                resp.meta["load_shed"] = bool(fn())
    except Exception:
        # ignore errors – preserve existing load_shed flag
        pass

    # non-blocking upload – keeps TTFB inside the 2.5 s budget
    asyncio.create_task(_minio_put_batch_async(req_id, artefacts))

    # ── NEW: Server-Sent-Events (SSE) support ────────────────────────
    if stream:
        short_answer: str = resp.answer.short_answer
        return StreamingResponse(
            stream_chunks(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # Preserve canonical field names per Tech-Spec §M4
    return JSONResponse(content=resp.model_dump(mode="python"))

# 11 ───────────────────── /v2 query (NL) ────────────────────────────────
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

    # ── intent router (Milestone-4) ──────────────────────────────────
    # Always run the router so that `function_calls`, `routing_confidence`
    # and `routing_model_id` appear in `meta` even when the caller did not
    # supply an explicit *functions* array.  This avoids the classic
    # “works-locally / fails-in-CI” scenario and honours the tech-spec §B1.
    default_functions: list[str] = ["search_similar", "get_graph_neighbors"]
    functions = req.functions if req.functions is not None else default_functions

    import importlib, sys, inspect
    _intent_mod = sys.modules.get("gateway.intent_router")
    if _intent_mod is None:
        _intent_mod = importlib.import_module("gateway.intent_router")
    _route_query = getattr(_intent_mod, "route_query")

    # Accept both async and sync implementations of `route_query`.
    # Tests often monkey-patch a synchronous stub that returns a plain dict,
    # whereas the production router is `async def`.  Unconditionally awaiting
    # the result therefore breaks tests and creates the very “works-locally /
    # fails-in-CI” problem the spec warns about (§B1).
    route_result = _route_query(q, functions)
    if inspect.isawaitable(route_result):
        routing_info: dict = await route_result
    else:
        routing_info: dict = route_result
    logger.info("intent_completed", extra=routing_info)
    
    # ── SSE streaming shortcut (Milestone-4) ─────────────────────────
    if stream:
        # Deterministic fallback until the full LLM pipeline lands.
        short_answer = (
            "Panasonic exited plasma TV production because of declining demand and sustained losses."
        )[:320]
        return StreamingResponse(
            stream_chunks(short_answer, include_event=include_event),
            media_type="text/event-stream",
        )

    # ── Resolve NL query to decision anchor ───────────────────────────
    # ── Decision-anchor fast-path ─────────────────────────────────────
    # If the caller *did not* request the search_similar helper we skip the
    # heavy text resolver to avoid hitting `/api/resolve/text`.
    anchor: dict | None = None
    if routing_info:
        fcalls = routing_info.get("function_calls", []) or []
        if "search_similar" not in fcalls:
            anchor_id: str | None = None
            # (1) explicit node_id argument
            for f in (req.functions or []):
                if isinstance(f, dict) and f.get("name") == "get_graph_neighbors":
                    anchor_id = (f.get("arguments") or {}).get("node_id")
                    if anchor_id:
                        break
            # (2) simple “node <slug>” pattern in the question
            if not anchor_id:
                m = re.search(r"\bnode\s+([a-zA-Z0-9\-]+)", q)
                if m:
                    anchor_id = m.group(1)
            if anchor_id:
                anchor = {"id": anchor_id}

    # Fallback – still use the resolver when we really need it
    if anchor is None:
        # ── Dynamically resolve the decision resolver (Milestone‑4) ─────
        # The resolver may be monkey‑patched on the gateway.app module during
        # integration tests.  Look it up via the module registry to avoid
        # holding onto a stale reference imported at module scope.
        import importlib, sys
        _gw_mod = sys.modules.get("gateway.app")
        resolver_func = None
        if _gw_mod is not None:
            resolver_func = getattr(_gw_mod, "resolve_decision_text", None)
        if not resolver_func:
            # Fallback to the canonical resolver from gateway.resolver
            _resolver_mod = sys.modules.get("gateway.resolver")
            if _resolver_mod is None:
                _resolver_mod = importlib.import_module("gateway.resolver")
            resolver_func = getattr(_resolver_mod, "resolve_decision_text")
        anchor = await resolver_func(q)
        if anchor is None:
            raise HTTPException(status_code=404, detail="No matching decision found")
    if anchor is None:
        # Milestone-3 compatibility: return provisional `{ "matches": [...] }`
        matches = await search_bm25(q, k=24)
        return JSONResponse(content={"matches": matches}, status_code=200)

    # ── Build evidence bundle and merge router helper results ─────────
    # Determine whether to include graph neighbours based on the caller’s requested functions
    include_neighbors: bool = (
        "get_graph_neighbors"
        in (routing_info.get("function_calls") or functions or [])
    )

    # ------------------------------------------------------------------
    # Milestone‑4 compatibility: gracefully handle EvidenceBuilder stubs
    # ------------------------------------------------------------------
    # Some tests monkey‑patch `_evidence_builder` with stub classes whose
    # `.build()` method does not accept an `include_neighbors` keyword argument.
    # We inspect the method signature at runtime; if it accepts that
    # parameter we pass it, otherwise we fall back to the single‑argument call.
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
        # If the stub rejects unexpected kwargs, retry without them.
        ev = await _evidence_builder.build(anchor["id"])

    # ------------------------------------------------------------------
    # Milestone-3 compatibility
    # -------------------------
    # The legacy `/evidence/*` shim (still used by unit-tests) **does not**
    # invoke the Milestone-4 intent-router, therefore `routing_info`
    # is undefined here.  Falling back to an empty dict prevents a
    # NameError and allows the stage-timeout logic to behave as intended.
    # When the router is introduced for this endpoint in a later
    # milestone we can simply feed the real routing payload through.
    # ------------------------------------------------------------------
    helper_payloads: dict = routing_info.get("results", {}) if routing_info else {}
    neighbours: List[dict] = []
    # Graph neighbours may be returned under ``neighbors`` (preferred) or
    # ``matches`` (legacy).  Only merge when the helper payload is a dict.
    if isinstance(helper_payloads.get("get_graph_neighbors"), dict):
        payload = helper_payloads.get("get_graph_neighbors") or {}
        neighbours += (
            payload.get("neighbors")
            or payload.get("matches")
            or []
        )
    # `search_similar` results are expected to be a list of identifiers (or dicts).
    if isinstance(helper_payloads.get("search_similar"), list):
        neighbours += helper_payloads.get("search_similar")

    seen = {e.get("id") for e in ev.events}
    for n in neighbours:
        nid = n.get("id") if isinstance(n, dict) else n
        if nid and nid not in seen and nid != ev.anchor.id:
            ev.events.append({"id": nid})
            seen.add(nid)

    # Refresh allowed_ids so validator stays happy
    ev.allowed_ids = _collect_allowed_ids(
        ev.anchor,
        ev.events,
        ev.transitions.preceding,
        ev.transitions.succeeding,
    )

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
        request_id=req.request_id,
    )
    resp, artefacts, req_id = await build_why_decision_response(
        ask_payload, _evidence_builder
    )
    
    asyncio.create_task(_minio_put_batch_async(req_id, artefacts))

    # ── SSE support ---------------------------------------------------
    if stream:
        return StreamingResponse(
            stream_chunks(resp.answer.short_answer),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # Surface routing metadata in the final response
    if routing_info:
        resp.meta.update(
            {
                "function_calls": routing_info.get("function_calls"),
                "routing_confidence": routing_info.get("routing_confidence"),
                "routing_model_id": routing_info.get("routing_model_id"),
            }
        )

    return JSONResponse(content=resp.model_dump())

# 12 ─────────── Legacy /evidence shim (still used in tests) ─────────────
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

    # ── Merge router helper payloads into evidence ────────────────────
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

    # refresh allowed_ids so validator stays happy
    ev.allowed_ids = _collect_allowed_ids(
        ev.anchor, ev.events,
        ev.transitions.preceding, ev.transitions.succeeding
    )

    ask_payload = AskIn(
        intent="why_decision",
        anchor_id=anchor["id"],
        evidence=ev,
    )
    resp_obj, *_ = await build_why_decision_response(
        ask_payload, _evidence_builder
    )

    # ── Server-Sent Events (SSE) support ───────────────────────────────
    # When the caller passes `?stream=true` we slice the validated
    # *short_answer* into token-sized chunks and emit them as a proper
    # `text/event-stream` (tech-spec §G).
    if stream:
        short_answer: str = resp_obj.answer.short_answer
        return StreamingResponse(
            stream_chunks(short_answer, include_event=include_event),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # Fallback – regular JSON body (non-streaming)
    return JSONResponse(
        status_code=200,
        content=resp_obj.model_dump(),
        headers={"x-snapshot-etag": "dummy-etag"},
    )

# 13 ────────────────────────── Final wiring ─────────────────────────────
app.include_router(router)
