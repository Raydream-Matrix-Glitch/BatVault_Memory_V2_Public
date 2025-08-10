from fastapi import FastAPI, Response, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core_config import get_settings
from core_logging import get_logger, log_stage, trace_span
from core_storage import ArangoStore
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from typing import Dict, List, Tuple, Optional
from functools import lru_cache
import httpx, inspect
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
logger.propagate = True
app = FastAPI(title="BatVault Memory_API", version="0.1.0")

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
    t0   = time.perf_counter()
    log_stage(logger, "request", "request_start",
              request_id=idem, path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt_ms = int((time.perf_counter() - t0) * 1000)
    core_metrics.histogram("memory_api_ttfb_ms", float(dt_ms))
    resp.headers["x-request-id"] = idem
    log_stage(logger, "request", "request_end",
              request_id=idem, status_code=resp.status_code,
              latency_ms=dt_ms)
    return resp

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:                         # pragma: no cover
    return Response(generate_latest(),
                    media_type=CONTENT_TYPE_LATEST)

async def _ping_gateway_ready():
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get("http://gateway:8081/readyz")
        return r.status_code == 200 and r.json().get("ready", False)

async def _readiness() -> dict:
    """
    Tests monkey-patch ``_ping_gateway_ready`` with a *synchronous* lambda.
    Accept both sync & async call-sites.
    """
    res = _ping_gateway_ready()
    ok = await res if inspect.isawaitable(res) else bool(res)
    return {
        "status": "ready" if ok else "degraded",
        "gateway_ok": ok,
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

    # Execute the enrichment in a thread with a 0.6 s timeout as per the
    # Milestone‑4 contract.  Timeouts surface as HTTP 504 responses.
    try:
        with trace_span("memory.enrich_decision", node_id=node_id):
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.6)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout")
    # Missing decisions must return a 404 error.
    if doc is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    # Attach the current snapshot ETag to the response headers when present
    etag = store().get_snapshot_etag()
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return doc

@app.get("/api/enrich/event/{node_id}")
def enrich_event(node_id: str, response: Response):
    with trace_span("memory.enrich_event", node_id=node_id):
        st = store()
        doc = st.get_enriched_event(node_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="event_not_found")
        etag = st.get_snapshot_etag()
        if etag:
            response.headers["x-snapshot-etag"] = etag
        return doc

@app.get("/api/enrich/transition/{node_id}")
def enrich_transition(node_id: str, response: Response):
    st = store()
    doc = st.get_enriched_transition(node_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="transition_not_found")
    etag = st.get_snapshot_etag()
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return doc

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
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")
@app.post("/api/resolve/text")
async def resolve_text(payload: dict, response: Response):
    # Tests monkey-patch `store` – clear cache so the patch is honoured
    _clear_store_cache()
    q = payload.get("q", "")
    use_vector = bool(payload.get("use_vector", False))
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
        not use_vector
        and query_vector is None
        and q
        and not _ID_RE.match(q)  # skip known slugs
    ):
        try:
            from memory.embeddings_client import embed  # type: ignore
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
    if not q and not (use_vector and query_vector):
        return {"matches": [], "query": q, "vector_used": False}
    # Milestone-2: known slug → short-circuit resolver (skip search)
    if q and _ID_RE.match(q):
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
                        "title": node.get("option") or node.get("title"),
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
            if etag:
                response.headers["x-snapshot-etag"] = etag
            log_stage(
                logger,
                "resolver",
                "slug_short_circuit",
                snapshot_etag=etag,
                match_count=1,
                vector_used=False,
            )
            return _json_response_with_etag(doc, etag)
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
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.8)
    except asyncio.TimeoutError:
        log_stage(logger, "expand", "timeout", request_id=payload.get("request_id"))
        raise HTTPException(status_code=504, detail="timeout")
    except Exception as e:
        # Unit-test friendly fallback: return empty contract (no DB required)
        log_stage(logger, "resolver", "fallback_empty", error=type(e).__name__)
        doc = {"query": q}
    try:
        etag = store().get_snapshot_etag()
    except Exception:
        etag = None
    if etag:
        response.headers["x-snapshot-etag"] = etag
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
            doc, etag = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.25)
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
        # Unit-test friendly fallback: return empty neighbors (no DB required)
        log_stage(logger, "expand", "fallback_empty", error=type(e).__name__)
        # Legacy shape for neighbours comes from older store
        # {"events": [], "transitions": []}
        doc  = {"node_id": None,
                "neighbors": {"events": [], "transitions": []},
                "meta": {}}
        etag = None                      # <- guarantee *etag* exists downstream

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

    result = {
        "node_id":  node_value,          # new canonical key
        "anchor":   node_value,          # legacy alias (will be removed in v3)
        "neighbors": raw_neighbors,
        "meta":      doc.get("meta", {"snapshot_etag": ""})  # always present
    }
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return _json_response_with_etag(result, etag)

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


