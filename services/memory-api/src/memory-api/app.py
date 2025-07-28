from fastapi import FastAPI
from fastapi.responses import JSONResponse
from core_config import get_settings
from core_logging import get_logger, log_event
import httpx, base64, json

settings = get_settings()
logger = get_logger("memory-api")

app = FastAPI(title="BatVault Memory API", version="0.1.0")

@app.on_event("startup")
async def bootstrap_arango():
    # Ensure DB, collections, and vector index if enabled.
    auth = (settings.arango_root_user, settings.arango_root_password)
    async with httpx.AsyncClient(base_url=settings.arango_url, auth=auth, timeout=20.0) as client:
        # Create database if not exists
        try:
            r = await client.post("/_api/database", json={"name": settings.arango_db})
            if r.status_code in (200, 201):
                log_event(logger, "arango_db_created", db=settings.arango_db)
            else:
                log_event(logger, "arango_db_create_skip_or_exists", status=r.status_code, body=r.text)
        except Exception as e:
            log_event(logger, "arango_db_create_error", error=str(e))

        # Use DB scope
        base = f"/_db/{settings.arango_db}"

        # Ensure collections
        for cname, ctype in [("nodes","document"), ("edges","edge")]:
            try:
                r = await client.post(f"{base}/_api/collection", json={"name": cname, "type": 3 if ctype=="edge" else 2})
                log_event(logger, "arango_collection_ensure", collection=cname, status=r.status_code)
            except Exception as e:
                log_event(logger, "arango_collection_error", collection=cname, error=str(e))

        # Ensure vector index (best-effort)
        if settings.arango_vector_index_enabled:
            try:
                # ArangoDB vector index creation payload (subject to version specifics)
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
                    "metric": settings.vector_metric,   # "cosine" | "euclidean" | "dot"
                    "engine": settings.vector_engine,   # "hnsw" | "flat"
                    "M": settings.hnsw_m,
                    "efConstruction": settings.hnsw_efconstruction
                }
                r = await client.post(f"{base}/_api/index?collection=nodes", json=payload)
                log_event(logger, "arango_vector_index_attempt", status=r.status_code, body=r.text[:200])
            except Exception as e:
                log_event(logger, "arango_vector_index_error", error=str(e))

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "memory-api"}

@app.get("/readyz")
def readyz():
    return {"ready": True}

# ---- Stubs for Milestone 0 ----
@app.get("/api/schema/field-catalog")
def field_catalog():
    return {"fields": []}

@app.get("/api/schema/relation-catalog")
def relation_catalog():
    return {"relations": []}

@app.post("/api/resolve/text")
def resolve_text(payload: dict):
    # Stub: return no matches yet
    return {"matches": [], "query": payload.get("q")}

@app.post("/api/graph/expand_candidates")
def expand_candidates(payload: dict):
    return {"anchor": payload.get("anchor"), "k": 1, "neighbors": []}
