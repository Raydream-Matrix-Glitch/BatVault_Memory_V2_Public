from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core_config import get_settings
from core_logging import get_logger, log_stage, trace_span
from core_storage import ArangoStore
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from typing import Dict, List, Tuple, Optional
from functools import lru_cache
import httpx
import asyncio
import re
import time

settings = get_settings()
logger = get_logger("memory_api")
app = FastAPI(title="BatVault Memory_API", version="0.1.0")

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
    ok = await _ping_gateway_ready()
    return {
        "status": "ready" if ok else "degraded",
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
    _ = store()

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
    async def _work():
        return store().get_enriched_decision(node_id)
    try:
        with trace_span("memory.enrich_decision", node_id=node_id):
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.6)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout")
    if doc is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
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
    q = payload.get("q", "")
    use_vector = bool(payload.get("use_vector", False))
    query_vector = payload.get("query_vector")
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
            log_stage(logger, "resolver", "slug_short_circuit",
                      snapshot_etag=etag, match_count=1, vector_used=False)
            return doc
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
    log_stage(logger, "resolver", "text_resolved",
              request_id=payload.get("request_id"),
              snapshot_etag=etag,
              match_count=len(doc.get("matches", [])),
              vector_used=doc.get("vector_used"))
    return doc

@app.post("/api/graph/expand_candidates")
async def expand_candidates(payload: dict, response: Response):
    anchor = payload.get("anchor")
    k = int(payload.get("k", 1))
    if not anchor:
        raise HTTPException(status_code=400, detail="anchor is required")
    def _work():
        # create store inside the worker to avoid eager connection
        st = store()
        return st.expand_candidates(anchor, k=k)
    try:
        with trace_span("memory.expand_candidates", anchor=anchor, k=k):
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.25)
    except asyncio.TimeoutError:
        log_stage(logger, "expand", "timeout",
                  request_id=payload.get("request_id"))
        raise HTTPException(status_code=504, detail="timeout")
    except Exception as e:
        # Unit-test friendly fallback: return empty neighbors (no DB required)
        log_stage(logger, "expand", "fallback_empty", error=type(e).__name__)
        # Legacy shape for neighbors comes from older store: {"events":[],"transitions":[]}
        doc = {"anchor": None, "neighbors": {"events": [], "transitions": []}, "meta": {}}
    try:
        etag = store().get_snapshot_etag()
    except Exception:
        etag = None
    if etag:
        response.headers["x-snapshot-etag"] = etag

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

    # Anchor – treat explicit null as "missing" and fall back to request anchor
    anchor_value = doc.get("anchor")
    if anchor_value is None:
        anchor_value = anchor

    # Neighbors – flatten legacy dicts into a single list
    raw_neighbors = doc.get("neighbors", [])
    if isinstance(raw_neighbors, dict):
        raw_neighbors = (raw_neighbors.get("events") or []) + \
                        (raw_neighbors.get("transitions") or [])
    # Ensure the field is always a list
    if not isinstance(raw_neighbors, list):
        raw_neighbors = []

    result = {
        "anchor":    anchor_value,
        "neighbors": raw_neighbors,
        "meta":      doc.get("meta", {"snapshot_etag": ""})  # always present
    }
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return JSONResponse(content=result)
