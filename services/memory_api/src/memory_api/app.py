from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import JSONResponse
from core_config import get_settings
from core_logging import get_logger, log_stage
from core_storage import ArangoStore
import httpx

settings = get_settings()
logger = get_logger("memory_api")
app = FastAPI(title="BatVault Memory API", version="0.1.0")

def store() -> ArangoStore:
    return ArangoStore(settings.arango_url, settings.arango_root_user, settings.arango_root_password,
                       settings.arango_db, settings.arango_graph_name,
                       settings.arango_catalog_collection, settings.arango_meta_collection)

@app.on_event("startup")
async def bootstrap_arango():
    # Ensure DB/collections via ArangoStore init
    _ = store()
    # Best-effort vector index bootstrap (HNSW) with audit logs
    if not settings.arango_vector_index_enabled:
        return
    base_url = f"{settings.arango_url}/_db/{settings.arango_db}"
    payload = {
        "type": "vector",
        "name": "idx_nodes_embedding_hnsw",
        "fields": ["embedding"],
        "inBackground": True,
        "unique": False,
        "sparse": True,
        "estimates": True,
        "storedValues": [],
        "vecType": "float32",
        "dimension": settings.embedding_dim,
        "metric": settings.vector_metric,    # cosine|euclidean|dot
        "engine": settings.vector_engine,    # hnsw|flat
        "M": settings.hnsw_m,
        "efConstruction": settings.hnsw_efconstruction,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{base_url}/_api/index?collection=nodes", json=payload)
            common = dict(
                status=r.status_code,
                dimension=settings.embedding_dim,
                metric=settings.vector_metric,
                engine=settings.vector_engine,
                m=settings.hnsw_m,
                efConstruction=settings.hnsw_efconstruction,
                index_name="idx_nodes_embedding_hnsw",
                collection="nodes",
            )
            if r.status_code in (200, 201):
                log_stage(logger, "bootstrap", "arango_vector_index_created", **common)
            elif r.status_code == 409:
                log_stage(logger, "bootstrap", "arango_vector_index_exists", **common)
            else:
                log_stage(logger, "bootstrap", "arango_vector_index_warn",
                          body=r.text[:200], **common)
    except Exception as e:
        log_stage(logger, "bootstrap", "arango_vector_index_error", error=str(e))

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "memory-api"}

@app.get("/readyz")
async def readyz():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.arango_url}/_api/version")
            if r.status_code == 200:
                return {"ready": True}
    except Exception:
        pass
    return {"ready": False}

# --------------- Catalogs ----------------
@app.get("/api/schema/fields")
def field_catalog(response: Response):
    st = store()
    etag = st.get_snapshot_etag()
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return {"fields": st.get_field_catalog()}

@app.get("/api/schema/rels")
def relation_catalog(response: Response):
    st = store()
    etag = st.get_snapshot_etag()
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return {"relations": st.get_relation_catalog()}

# --------------- Enrichment -------------
@app.get("/api/enrich/decision/{node_id}")
def enrich_decision(node_id: str, response: Response):
    st = store()
    doc = st.get_enriched_decision(node_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    etag = st.get_snapshot_etag()
    if etag:
        response.headers["x-snapshot-etag"] = etag
    return doc

@app.get("/api/enrich/event/{node_id}")
def enrich_event(node_id: str, response: Response):
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

# --------------- Stubs preserved for M2 -------------
@app.post("/api/resolve/text")
def resolve_text(payload: dict):
    # M2 will implement BM25/vector resolve; stub for now
    return {"matches": [], "query": payload.get("q")}

@app.post("/api/graph/expand_candidates")
def expand_candidates(payload: dict):
    # M2 will implement real AQL traversal (k=1)
    return {"anchor": payload.get("anchor"), "k": 1, "neighbors": []}
