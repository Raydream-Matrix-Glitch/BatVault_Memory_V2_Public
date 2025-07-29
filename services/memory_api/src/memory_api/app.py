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
    # Ensure DB/collections via ArangoStore init (it handles vector index creation)
    _ = store()

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
