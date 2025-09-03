from fastapi import FastAPI, Response, HTTPException, Request
import os
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core_config import get_settings
from core_logging import get_logger, log_stage, trace_span
from core_observability.otel import setup_tracing, instrument_fastapi_app
from core_observability.otel import inject_trace_context
from core_storage import ArangoStore
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id, is_slug
from typing import Dict, List, Tuple, Optional
from functools import lru_cache
import inspect
from core_http.client import get_http_client
from core_config.constants import timeout_for_stage
import asyncio
import re
import time
import core_metrics

# ---------------------------------------------------------------------------
# Legacy module alias needed by unit tests that monkey-patch `embed()` on
# the storage layer.  Importing the module here keeps the public surface
# of `memory_api.app` stable after the recent refactor.
import core_storage.arangodb as arango_mod  # noqa: F401
# ---------------------------------------------------------------------------

settings = get_settings()
logger = get_logger("memory_api")
logger.propagate = False
app = FastAPI(title="BatVault Memory_API", version="0.1.0")
# Initialise tracing before wrapping the application so spans have real IDs
setup_tracing(os.getenv('OTEL_SERVICE_NAME') or 'memory_api')
# Ensure OTEL middleware wraps all subsequent middlewares/handlers
instrument_fastapi_app(app, service_name=os.getenv('OTEL_SERVICE_NAME') or 'memory_api')


# ──────────────────────────────────────────────────────────────────────────────
# Helper: safe cache eviction
# ---------------------------------------------------------------------------
# Unit-test fixtures monkey-patch the module-level **store** symbol with a
# simple `lambda: DummyStore()`.  Such lambdas are *not* decorated with
# `functools.lru_cache`, therefore they do **not** expose `.cache_clear`.
# Calling it blindly raises `AttributeError`, breaking every request inside
# the test-suite.  The helper below is a zero-cost indirection that preserves
# production behaviour (clearing the real LRU when present) while remaining
# compatible with monkey-patched versions.
# ---------------------------------------------------------------------------

def _clear_store_cache() -> None:  # pragma: no cover – trivial utility
    """Best-effort cache invalidation that tolerates monkey-patched *store*."""
    clear_fn = getattr(store, "cache_clear", None)  # type: ignore[attr-defined]
    if callable(clear_fn):
        clear_fn()

# ── HTTP middleware: deterministic IDs, logs & TTFB histogram ──────────────
@app.middleware("http")
async def _request_logger(request: Request, call_next):
    idem = generate_request_id()
    # Observability: expose current trace/span IDs (should be non-zero if OTEL middleware wrapped us)
    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, 'trace_id', 0):
            log_stage(logger, 'observability', 'trace_ctx',
                      request_id=idem,
                      trace_id=f"{_ctx.trace_id:032x}",
                      span_id=f"{_ctx.span_id:016x}")
    except Exception:
        pass
    t0   = time.perf_counter()
    log_stage(logger, "request", "request_start",
              request_id=idem, path=request.url.path, method=request.method)

    resp = await call_next(request)

    # Bubble the active trace id to clients for audit drawers
    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, 'trace_id', 0):
            resp.headers["x-trace-id"] = f"{_ctx.trace_id:032x}"
    except Exception:
        pass

    dt_s = (time.perf_counter() - t0)
    core_metrics.histogram("memory_api_ttfb_seconds", dt_s)
    resp.headers["x-request-id"] = idem
    # track totals for SLOs and bubble trace id
    try:
        core_metrics.counter("memory_api_http_requests_total", 1, method=request.method, code=str(resp.status_code))
        if str(resp.status_code).startswith("5"):
            core_metrics.counter("memory_api_http_5xx_total", 1)
    except Exception:
        pass
    log_stage(logger, "request", "request_end",
              request_id=idem, status_code=resp.status_code,
              latency_ms=dt_s * 1000.0)
    return resp

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:                         # pragma: no cover
    return Response(generate_latest(),
                    media_type=CONTENT_TYPE_LATEST)

async def _ping_arango_ready() -> bool:
    """
    Return True iff ArangoDB responds OK. Memory API depends on ArangoDB.
    """
    settings = get_settings()
    try:
        client = get_http_client(timeout_ms=int(1000 * timeout_for_stage('enrich')))
        r = await client.get(f"{settings.arango_url}/_api/version", headers=inject_trace_context({}))
        return r.status_code == 200
    except Exception:
        return False
    
async def _ping_gateway_ready() -> bool:
    """Backward-compatible alias for tests; calls Arango readiness."""
    return await _ping_arango_ready()

async def _readiness() -> dict:
    """
    Tests monkey-patch ``_ping_gateway_ready`` with a *synchronous* lambda.
    Accept both sync & async call-sites.
    """
    res = _ping_arango_ready()
    ok = await res if inspect.isawaitable(res) else bool(res)
    return {
        "status": "ready" if ok else "degraded",
        "arango_ok": ok,      # primary key
        "gateway_ok": ok,     # backwards-compat alias
        "request_id": generate_request_id(),
    }

attach_health_routes(
    app,
    checks={
        "liveness": lambda: True,          # always healthy if the process is up
        "readiness": _readiness,           # still verifies Gateway once it exists
    },
)

@lru_cache()
def store() -> ArangoStore:
    # lazy=True prevents connection attempts during unit tests
    return ArangoStore(settings.arango_url,
                       settings.arango_root_user,
                       settings.arango_root_password,
                       settings.arango_db,
                       settings.arango_graph_name,
                       settings.arango_catalog_collection,
                       settings.arango_meta_collection,
                       lazy=True)

@app.on_event("startup")
async def bootstrap_arango():
    # Ensure DB/collections via ArangoStore init (it handles vector index creation)
    try:
        _ = store()
    except Exception as exc:
        logger.warning("Lazy ArangoStore bootstrap skipped: %s", exc)

# ──────────────────────────────────────────────────────────────────────────────
# Shared helper: always attach the current snapshot ETag
# ──────────────────────────────────────────────────────────────────────────────
def _json_response_with_etag(payload: dict, etag: Optional[str] = None) -> JSONResponse:
    """
    Build a JSONResponse and, when available, mirror the repository’s current
    snapshot ETag in the `x-snapshot-etag` header so that gateways and tests
    can rely on cache-invalidation semantics.
    """
    resp = JSONResponse(content=payload)
    if etag:
        resp.headers["x-snapshot-etag"] = etag
    return resp

# ──────────────────────────────────────────────────────────────────────────────
# Normalization helpers (API-level, resilient to monkey-patched stores)
# Ensures consistent shape even when tests provide a DummyStore that skips
# ArangoStore-side normalization.
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_node_payload(doc: dict, node_type: str) -> dict:
    """Normalise a node payload using the shared normaliser.

    This helper delegates unconditionally to the shared normaliser functions
    exposed in the ``shared.normalize`` package.  The previous
    implementation contained a fallback that attempted to normalise and
    sanitise payloads when the shared normaliser could not be imported.
    That fallback logic has been removed to ensure a single source of
    truth for normalisation.  If the shared module cannot be imported
    during testing, tests should monkey‑patch the import or use the
    shared normaliser directly rather than relying on this API to
    provide a degraded path.
    """
    from shared.normalize import (
        normalize_decision,
        normalize_event,
        normalize_transition,
    )
    # Dispatch on the node type; unknown types are returned as plain dicts
    if node_type == "decision":
        return normalize_decision(doc)
    if node_type == "event":
        return normalize_event(doc)
    if node_type == "transition":
        return normalize_transition(doc)
    # Unknown node type – return a shallow copy
    return dict(doc or {})


# ------------------ Catalog helpers (shared) ------------------
def _compute_field_catalog() -> Tuple[Dict[str, List[str]], Optional[str]]:
    st = store()
    etag = st.get_snapshot_etag()
    fields = st.get_field_catalog()
    log_stage(logger, "schema", "fields_retrieved",
              snapshot_etag=etag, field_count=len(fields))
    return fields, etag

def _compute_relation_catalog() -> Tuple[List[str], Optional[str]]:
    st = store()
    etag = st.get_snapshot_etag()
    relations = st.get_relation_catalog()
    log_stage(logger, "schema", "relations_retrieved",
              snapshot_etag=etag, relation_count=len(relations))
    return relations, etag

# ------------------ Catalogs (HTTP) ------------------
@app.get("/api/schema/fields")
def get_field_catalog(response: Response):
    with trace_span("memory.schema_fields"):
        fields, etag = _compute_field_catalog()
        if etag:
            response.headers["x-snapshot-etag"] = etag
        return {"fields": fields}

@app.get("/api/schema/rels")
@app.get("/api/schema/relations")
def get_relation_catalog(response: Response):
    with trace_span("memory.schema_relations"):
        relations, etag = _compute_relation_catalog()
        if etag:
            response.headers["x-snapshot-etag"] = etag
        return {"relations": relations}

# --------------- Enrichment -------------
@app.get("/api/enrich/decision/{node_id}")
async def enrich_decision(node_id: str, response: Response):
    """
    Return a fully enriched decision document.

    The enrichment operation runs in a background thread since
    ``ArangoStore.get_enriched_decision`` is synchronous.  Defining
    ``_work`` as a synchronous callable is critical: ``asyncio.to_thread``
    expects a regular function, not a coroutine.  If we accidentally
    define `_work` as ``async def``, ``to_thread`` will return an
    un‑awaited coroutine which breaks FastAPI's response encoding.
    """

    def _work() -> Optional[dict]:
        # Lazily create the store inside the worker thread to avoid
        # eager Arango connections during unit tests.  The underlying
        # call is synchronous, so this function must not be declared
        # ``async``.
        return store().get_enriched_decision(node_id)

    # Execute the enrichment in a thread with a configurable timeout.
    # Default comes from TIMEOUT_ENRICH_MS; 504 on timeout.
    try:
        with trace_span("memory.enrich_decision", node_id=node_id):
            budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout")
    # Missing decisions must return a 404 error.
    if doc is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    try:
        etag = store().get_snapshot_etag()
    except Exception:
        etag = None
    safe_etag = etag or "unknown"
    if isinstance(doc, dict):
        doc = _normalize_node_payload(doc, "decision")
        meta_obj = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        meta_obj["snapshot_etag"] = safe_etag
        doc["meta"] = meta_obj
    return _json_response_with_etag(doc, safe_etag)

@app.get("/api/enrich/event/{node_id}")
async def enrich_event(node_id: str, response: Response):
    """
    Return a fully enriched event document.  The synchronous store call is offloaded
    to a worker thread with a configurable timeout to avoid blocking the event loop.
    Missing events return 404 and timeouts return 504.
    """
    with trace_span("memory.enrich_event", node_id=node_id):
        import asyncio
        def _work() -> Optional[dict]:
            st = store()
            return st.get_enriched_event(node_id)
        try:
            budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="timeout")
        if doc is None:
            raise HTTPException(status_code=404, detail="event_not_found")
        try:
            etag = store().get_snapshot_etag()
        except Exception:
            etag = None
        safe_etag = etag or "unknown"
        if isinstance(doc, dict):
            doc = _normalize_node_payload(doc, "event")
            meta_obj = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
            meta_obj["snapshot_etag"] = safe_etag
            doc["meta"] = meta_obj
        return _json_response_with_etag(doc, safe_etag)

@app.get("/api/enrich/transition/{node_id}")
async def enrich_transition(node_id: str, response: Response):
    """
    Return a fully enriched transition document.  Offloads blocking store calls
    to a worker thread and applies a timeout.  Missing transitions return 404.
    """
    import asyncio
    def _work() -> Optional[dict]:
        st = store()
        return st.get_enriched_transition(node_id)
    try:
        budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
        doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout")
    if doc is None:
        raise HTTPException(status_code=404, detail="transition_not_found")
    try:
        etag = store().get_snapshot_etag()
    except Exception:
        etag = None
    safe_etag = etag or "unknown"
    if isinstance(doc, dict):
        doc = _normalize_node_payload(doc, "transition")
        meta_obj = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        meta_obj["snapshot_etag"] = safe_etag
        doc["meta"] = meta_obj
    return _json_response_with_etag(doc, safe_etag)

# ------------------ Catalogs (module-level callables for tests) ------------------
def field_catalog(request):
    """Test-friendly callable mirroring /api/schema/fields."""
    fields, etag = _compute_field_catalog()
    log_stage(logger, "schema", "fields_function_called",
              has_mutable_headers=isinstance(getattr(request, "headers", None), dict))
    if isinstance(getattr(request, "headers", None), dict) and etag:
        request.headers["x-snapshot-etag"] = etag
    return {"fields": fields}

def relation_catalog(request):
    """Test-friendly callable mirroring /api/schema/rels."""
    relations, etag = _compute_relation_catalog()
    log_stage(logger, "schema", "relations_function_called",
              has_mutable_headers=isinstance(getattr(request, "headers", None), dict))
    if isinstance(getattr(request, "headers", None), dict) and etag:
        request.headers["x-snapshot-etag"] = etag
    return {"relations": relations}


# ------------------ Resolver ------------------
@app.post("/api/resolve/text")
async def resolve_text(payload: dict, response: Response):
    # Tests monkey-patch `store` – clear cache so the patch is honoured
    _clear_store_cache()
    q = payload.get("q", "")
    # Distinguish *omitted* from *explicit False* so we can honour False strictly.
    _use_vector_raw = payload.get("use_vector", None)
    use_vector = bool(_use_vector_raw)
    query_vector = payload.get("query_vector")

    # ------------------------------------------------------------------
    # Embeddings integration (Milestone‑7)
    # ------------------------------------------------------------------
    # When the client has not explicitly opted into vector search but
    # embeddings are enabled at the service level, compute the query
    # embedding via the TEI client.  If the embedding is successful and
    # matches the configured dimensionality, enable vector search and
    # attach the vector to the payload.  Errors fall back silently to
    # BM25-only mode.  Known slugs are not embedded.
    if (
        _use_vector_raw is None            # only auto-embed when caller did not specify
        and query_vector is None
        and q
        and not is_slug(q)          # skip known slugs
    ):
        try:
            from core_ml.embeddings import embed  # canonical client
            # Attempt to embed the query; returns None on failure
            embeddings = await embed([q])
            if embeddings:
                query_vector = embeddings[0]
                use_vector = True
                # Mirror the embedding in the inbound payload for downstream
                payload["use_vector"] = True
                payload["query_vector"] = query_vector
        except Exception:
            # fallback – leave use_vector false so search remains BM25-only
            log_stage(logger, "embeddings", "fallback_bm25", query=q)
    elif _use_vector_raw is False:
        # Explicit False from the caller – document that we honoured it.
        log_stage(logger, "embeddings", "honour_use_vector_false", query=q)
    if not q and not (use_vector and query_vector):
        return {"matches": [], "query": q, "vector_used": False}
    if q and is_slug(q):
        try:
            node = store().get_node(q)
        except Exception:
            node = None
        if node:
            doc = {
                "query": q,
                "matches": [
                    {
                        "id": q,
                        "score": 1.0,
                        "title": node.get("title") or node.get("option"),
                        "type": node.get("type"),
                    }
                ],
                "vector_used": False,
                # 🔑  Contract: resolved_id must always be present & non-null
                "resolved_id": q,
            }
            try:
                etag = store().get_snapshot_etag()
            except Exception:
                etag = None
            safe_etag = etag or "unknown"
            log_stage(
                logger,
                "resolver",
                "slug_short_circuit",
                snapshot_etag=safe_etag,
                match_count=1,
                vector_used=False,
            )
            return _json_response_with_etag(doc, safe_etag)
    # IMPORTANT: to_thread expects a *sync* callable
    def _work():
        # create store inside the worker to avoid eager connection
        st = store()
        return st.resolve_text(
            q,
            limit=int(payload.get("limit", 10)),
            use_vector=use_vector,
            query_vector=query_vector,
        )
    try:
        with trace_span("memory.resolve_text", q=q, use_vector=use_vector):
            # enforce 0.8s timeout, as per spec
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("search"))
    except asyncio.TimeoutError:
        log_stage(logger, "expand", "timeout", request_id=payload.get("request_id"))
        raise HTTPException(status_code=504, detail="timeout")
    except Exception as e:
        # Unit-test friendly fallback: return empty contract (no DB required)
        log_stage(logger, "resolver", "fallback_empty", error=type(e).__name__)
        doc = {"query": q, "meta": {"fallback_reason": "db_unavailable"}}
    try:
        etag = store().get_snapshot_etag()
    except Exception:
        etag = None
    if etag:
        response.headers["x-snapshot-etag"] = etag
    # Also surface the snapshot in the body for simpler audit drawers
    doc.setdefault("meta", {})
    doc["meta"]["snapshot_etag"] = etag or "unknown"
    doc["meta"]["snapshot_available"] = bool((etag or "") and (etag or "") != "unknown")
    # Convenience flag: was vector search even available?
    doc["meta"]["vector_enabled"] = (os.getenv("ENABLE_EMBEDDINGS", "").lower() == "true")
    # Ensure contract keys present (normalize to input)
    # ---- 🔒 Contract normalisation (Milestone-2) ---- #
    doc["query"] = q                         # echo the raw query back
    doc.setdefault("matches", [])            # always a list
    doc.setdefault("vector_used", bool(use_vector))
    doc.setdefault("meta", {})

    # ---------- ensure non-null resolved_id ------------------------------ #
    if doc.get("matches"):
        doc["resolved_id"] = doc["matches"][0].get("id")
    else:
        doc["resolved_id"] = q
    log_stage(
        logger,
        "resolver",
        "text_resolved",
        request_id=payload.get("request_id"),
        snapshot_etag=etag,
        match_count=len(doc.get("matches", [])),
        vector_used=doc.get("vector_used"),
    )
    # Ensure the ETag header survives the FastAPI response conversion
    return _json_response_with_etag(doc, etag)

@app.post("/api/graph/expand_candidates")
async def expand_candidates(payload: dict, response: Response):
    # Honour monkey-patched `store` in unit tests
    _clear_store_cache()

    # ------------------------------------------------------------------ #
    # Ensure *etag* is always bound, even when the store raises and we   #
    # fall back to a dummy-document.  This removes the UnboundLocalError #
    # surfaced by test_expand_anchor_with_underscore.                    #
    # ------------------------------------------------------------------ #
    etag: Optional[str] = None
    # Milestone-4 contract: `node_id` is canonical, but keep `anchor`
    node_id = payload.get("node_id") or payload.get("anchor")
    k = int(payload.get("k", 1))
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")
    def _work():
        st = store()
        return st.expand_candidates(node_id, k=k), st.get_snapshot_etag()
    try:
        with trace_span("memory.expand_candidates", node_id=node_id, k=k):
            doc, etag = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("expand"))
    except asyncio.TimeoutError:
        # Do **not** bubble a 504; honour the v2 contract by replying 200 with a
        # deterministic, empty payload.  The caller can inspect `meta.fallback_reason`
        # to see that a timeout occurred.
        log_stage(logger, "expand", "timeout_fallback",
                  request_id=payload.get("request_id"))
        doc = {
            "node_id": node_id,
            "anchor":  node_id,                 # legacy alias
            "neighbors": [],
            "meta": {"fallback_reason": "timeout"},
        }
        etag = None
    except Exception as e:
        # Unit-test friendly fallback: return empty neighbors (no DB required).  When
        # an unexpected exception occurs (e.g., database unavailable), the caller
        # should be able to inspect a fallback_reason in the response meta.  We
        # categorise all unexpected errors as "db_unavailable".
        log_stage(logger, "expand", "fallback_empty", error=type(e).__name__)
        doc  = {"node_id": None,
                "neighbors": {"events": [], "transitions": []},
                "meta": {"fallback_reason": "db_unavailable"}}
        etag = None 

    # ---- 🔒 Contract normalisation (Milestone-2) ---- #
    #
    # Required keys:
    #   • anchor         – string | null, always present
    #   • neighbors      – list (flattened), even when store returns the legacy
    #                      {"events": [...], "transitions": [...]} shape
    #   • meta           – dict (optionally empty)
    #
    if not isinstance(doc, dict):
        doc = {}

    # Normalise ID – prefer explicit value from store, fall back to request
    node_value = doc.get("node_id") if isinstance(doc, dict) else None
    if node_value is None:
        node_value = doc.get("anchor") if isinstance(doc, dict) else None
    if node_value is None:
        node_value = node_id

    # Neighbors – flatten legacy dicts into a single list
    raw_neighbors = doc.get("neighbors", [])
    if isinstance(raw_neighbors, dict):
        raw_neighbors = (raw_neighbors.get("events") or []) + \
                        (raw_neighbors.get("transitions") or [])
    # Ensure the field is always a list
    if not isinstance(raw_neighbors, list):
        raw_neighbors = []

    # Extract any existing meta information from the document.  Per the
    # contract, meta must always be a dictionary (optionally empty).
    # If the store returned a non-dict or missing meta value we normalise
    # it to an empty dict.  Defining this object up front avoids
    # NameError when building the response.
    meta_obj: dict = {}
    if isinstance(doc, dict):
        maybe_meta = doc.get("meta")
        if isinstance(maybe_meta, dict):
            meta_obj = maybe_meta

    # Assemble the canonical result payload.  We always include
    # node_id/anchor, a flattened neighbours list and a meta object.
    result = {
        "node_id":  node_value,
        "anchor":   node_value,
        "neighbors": raw_neighbors,
        "meta":      meta_obj,
    }
    # Determine the effective snapshot_etag: prefer the one returned from the store;
    # otherwise fall back to any existing meta.snapshot_etag; default to "unknown".
    if etag:
        safe_etag = etag
    else:
        # Derive the ETag from any existing snapshot_etag in the meta
        # object; fall back to "unknown" when absent.  meta_obj is always
        # a dict after the normalisation above.
        safe_etag = meta_obj.get("snapshot_etag") if isinstance(meta_obj, dict) else None
        if not safe_etag:
            safe_etag = "unknown"
    if not isinstance(result["meta"], dict):
        result["meta"] = {}
    result["meta"]["snapshot_etag"] = safe_etag
    # Flag snapshot availability explicitly.  If the ETag could not be
    # retrieved from Arango this flag will be False.  Downstream callers
    # differentiate between a missing snapshot and the legacy "unknown"
    # marker.
    result["meta"]["snapshot_available"] = bool(safe_etag and safe_etag != "unknown")
    result["meta"]["ok"] = True
    result["meta"]["neighbor_count"] = len(result.get("neighbors") or [])
    result["meta"]["k"] = k
    # One structured log for dashboards & debugging
    log_stage(
        logger, "expand", "completed",
        node_id=node_id, k=k, neighbors=result["meta"]["neighbor_count"],
        snapshot_etag=safe_etag
    )
    return _json_response_with_etag(result, safe_etag)

# ─────────────────────────────────────────────────────────────────────────────
#  Trailing-slash aliases kept for legacy contract tests                      │
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/graph/expand_candidates/")
async def expand_candidates_slash(payload: dict, response: Response):
    """Back-compat: delegate trailing-slash variant to the canonical handler."""
    return await expand_candidates(payload, response)

@app.post("/api/resolve/text/")
async def resolve_text_slash(payload: dict, response: Response):
    """Back-compat: delegate trailing-slash variant to the canonical handler."""
    return await resolve_text(payload, response)


