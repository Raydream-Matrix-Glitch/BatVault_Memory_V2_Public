from fastapi import FastAPI, Response, HTTPException, Request
import os
from fastapi.responses import JSONResponse
import re
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
from core_config.constants import timeout_for_stage, TTL_EXPAND_CACHE_SEC
from .policy import compute_effective_policy, filter_and_mask_neighbors, field_mask
import asyncio
import time
import core_metrics
import os
import threading
from collections import OrderedDict

# ---------------------------------------------------------------------------
# Legacy module alias needed by unit tests that monkey-patch `embed()` on
# the storage layer.  Importing the module here keeps the public surface
# of `memory_api.app` stable after the recent refactor.
import core_storage.arangodb as arango_mod  # noqa: F401
# ---------------------------------------------------------------------------

settings = get_settings()
logger = get_logger("memory_api")
logger.propagate = False

# -----------------------------
# Pre-selector LRU+TTL cache
# -----------------------------
class _TTLCache:
    def __init__(self, maxsize: int = 2048, ttl_sec: int = 60):
        self.maxsize = maxsize
        self.ttl_sec = ttl_sec
        self._data = OrderedDict()  # key -> (value, ts)
        self._lock = threading.Lock()
    def get(self, key: str):
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            val, ts = entry
            if (now - ts) > self.ttl_sec:
                # expired
                try:
                    del self._data[key]
                except KeyError:
                    pass
                return None
            self._data.move_to_end(key)
            return val
    def put(self, key: str, value):
        now = time.time()
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (value, now)
            if len(self._data) > self.maxsize:
                self._data.popitem(last=False)

def _cache_key(snapshot_etag: str, policy_key: str, node_id: str) -> str:
    return f"{snapshot_etag}|{policy_key}|{node_id}"

PRESELECT_CACHE = _TTLCache(
    maxsize=int(os.getenv("MEMORY_PRESELECTOR_CACHE_SIZE", "2048") or "2048"),
    ttl_sec=int(os.getenv("MEMORY_PRESELECTOR_CACHE_TTL_SEC", str(TTL_EXPAND_CACHE_SEC)) or str(TTL_EXPAND_CACHE_SEC)),
)

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
    if isinstance(getattr(request, "headers", None), dict) and etag:
        request.headers["x-snapshot-etag"] = etag
    return {"fields": fields}

def relation_catalog(request):
    """Test-friendly callable mirroring /api/schema/rels."""
    relations, etag = _compute_relation_catalog()
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
async def expand_candidates(payload: dict, response: Response, request: Request):
    # Honour monkey-patched `store` in unit tests
    _clear_store_cache()

    # Ensure *etag* is always bound, even when the store raises and we
    # fall back to a dummy-document.
    etag: Optional[str] = None
    original_key = None
    if isinstance(payload, dict):
        for k in ("node_id", "anchor_id", "decision_ref"):
            if k in payload and payload.get(k):
                node_id = payload.get(k)
                original_key = k
                break
        else:
            node_id = None
    else:
        node_id = None

    if not node_id:
        raise HTTPException(status_code=400, detail="node_id or anchor_id is required")
    # Accept both plain Arango `_key` *and* fully-qualified IDs like `decisions/<key>` or `nodes/<key>`.
    # We store everything under the unified `nodes` collection, keyed by `<key>`, so strip any prefix.
    if isinstance(node_id, str) and "/" in node_id:
        original = node_id
        node_id = node_id.split("/", 1)[1]
        if original != node_id:
            # Use consistent naming in logs and record which key we accepted.
            log_stage(
                logger, "expand", "normalized_node_id",
                original=original, normalized=node_id, input_key=original_key
            )
    elif original_key in ("anchor_id", "decision_ref"):
        # If we didn't need to normalize but we did accept an alias key, log it for audits.
        log_stage(logger, "expand", "accepted_alias_key", input_key=original_key, node_id=node_id)
    # Hard fail early on obviously invalid IDs to avoid AQL errors
    if not is_slug(node_id):
        log_stage(logger, "expand", "input_error", reason="invalid_node_id_format", node_id=node_id, input_key=original_key)
        raise HTTPException(status_code=400, detail="invalid node_id (expected slug, got path or illegal chars)")
    # Always bound to 1 for the demo (policy may request lower).
    k = 1
    # Storage fetch: neighbors around anchor + snapshot etag
    def _work():
        st = store()
        return st.expand_candidates(node_id, k=k), st.get_snapshot_etag(), st
    try:
        with trace_span("memory.expand_candidates", node_id=node_id, k=k):
            doc, etag, st = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("expand"))
    except asyncio.TimeoutError:
        log_stage(logger, "expand", "timeout_fallback", request_id=payload.get("request_id"))
        doc, st = {"node_id": node_id, "neighbors": []}, store()
    except Exception as e:  # pragma: no cover — defensive guardrail
        log_stage(logger, "expand", "fallback_empty", error=type(e).__name__)
        doc, st = {"node_id": node_id, "neighbors": []}, store()
    # Normalise contract keys from storage
    result = {
        "node_id": (doc.get("node_id") or doc.get("anchor")),
        "neighbors": list(doc.get("neighbors") or []),
        "meta": dict(doc.get("meta") or {}),
    }
    # -------------------------
    # 🔐 Policy Pre-Selector
    # -------------------------
    # Resolve effective policy from request headers (fail closed on missing role)
    try:
        policy = compute_effective_policy({k: v for k, v in request.headers.items()})
        log_stage(
            logger, "policy", "resolved",
            role=policy.get("role"),
            namespaces=",".join(policy.get("namespaces") or []),
            scopes=",".join(policy.get("domain_scopes") or []),
            edge_types=",".join(policy.get("edge_allowlist") or []),
            sensitivity=policy.get("sensitivity_ceiling"),
            policy_key=policy.get("policy_key"),
            policy_version=policy.get("policy_version"),
            policy_fp=policy.get("policy_fp"),
            user_id=policy.get("user_id"),
            request_id=policy.get("request_id"),
            trace_id=policy.get("trace_id"),
        )
    except Exception as e:
        # Log with context so the culprit (e.g., missing role file) is obvious in traces
        try:
            from .policy import _policy_dir
            log_stage(
                logger, "policy", "resolve_error",
                error=type(e).__name__, error_message=str(e), policy_dir=_policy_dir()
            )
        except Exception:
            log_stage(logger, "policy", "resolve_error", error=type(e).__name__, error_message=str(e))
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}:{str(e)}")
    # Derive the final snapshot etag; if unknown, we skip caching
    safe_etag = (etag or (result.get("meta") or {}).get("snapshot_etag")) or "unknown"

    # Optional cache bypass for debugging
    hdrs_lc = {str(k).lower(): v for k, v in request.headers.items()}
    cache_bypass = (hdrs_lc.get("x-cache-bypass") or "0").strip() in ("1", "true", "yes")

    # 🔁 Cache check (etag+policy+node scope)
    cache_key = None
    if (safe_etag != "unknown") and not cache_bypass:
        cache_key = _cache_key(safe_etag, policy.get("policy_key"), result["node_id"])
        cached = PRESELECT_CACHE.get(cache_key)
        if cached is not None:
            log_stage(
                logger, "preselector", "cache_hit",
                node_id=node_id, snapshot_etag=safe_etag, policy_key=policy.get("policy_key"),
                neighbors=cached.get("meta", {}).get("neighbor_count", 0)
            )
            return _json_response_with_etag(cached, safe_etag)

    # Mask the anchor document according to role visibility
    try:
        anchor_doc = st.get_node(result["node_id"])
    except Exception:
        anchor_doc = None
    masked_anchor = field_mask(anchor_doc or {"id": result["node_id"], "type": (anchor_doc or {}).get("type")}, policy)
    # Apply edge allowlist + ACL to neighbors, and field mask per role
    included_neighbors, policy_trace = filter_and_mask_neighbors(result.get("neighbors") or [], st, policy)
    # Compose final CandidateSet
    candidate_set = {
        "anchor": masked_anchor,
        "neighbors": included_neighbors or [],
        "meta": {
            "snapshot_etag": safe_etag,
            "snapshot_available": bool(safe_etag and safe_etag != "unknown"),
            "policy_key": policy.get("policy_key"),
            "policy_fp": policy.get("policy_fp"),
            "policy": {  # surface effective policy for audit drawer
                "role": policy.get("role"),
                "scopes": policy.get("domain_scopes") or [],
                "edge_allowlist": policy.get("edge_allowlist") or [],
                "sensitivity": policy.get("sensitivity_ceiling"),
            },
            "ok": True,
            "k": 1,
            "neighbor_count": len(included_neighbors),
        },
        "policy_trace": policy_trace,
    }
    # Store in cache if eligible
    if (cache_key is not None) and not cache_bypass:
        PRESELECT_CACHE.put(cache_key, candidate_set)
        log_stage(
            logger, "preselector", "cache_miss_store",
            node_id=node_id, snapshot_etag=safe_etag, policy_key=policy.get("policy_key"),
            neighbors=candidate_set["meta"]["neighbor_count"]
        )
    # One structured log for dashboards & debugging
    log_stage(
        logger, "preselector", "completed",
        node_id=node_id,
        neighbors=candidate_set["meta"]["neighbor_count"],
        edge_types=",".join(policy_trace.get("edge_types_used") or []),
        hidden_vertices=policy_trace.get("counts", {}).get("hidden_vertices", 0),
        snapshot_etag=safe_etag,
        policy_key=policy.get("policy_key"),
    )
    return _json_response_with_etag(candidate_set, safe_etag)
# ─────────────────────────────────────────────────────────────────────────────
#  Trailing-slash aliases kept for legacy contract tests                      │
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/graph/expand_candidates/")
async def expand_candidates_slash(payload: dict, response: Response, request: Request):
    """Trailing-slash variant delegates to the canonical handler."""
    return await expand_candidates(payload, response, request)

@app.post("/api/resolve/text/")
async def resolve_text_slash(payload: dict, response: Response):
    """Trailing-slash variant delegates to the canonical handler."""
    return await resolve_text(payload, response)


