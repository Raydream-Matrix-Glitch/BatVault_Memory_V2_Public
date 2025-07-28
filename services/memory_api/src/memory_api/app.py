from fastapi import FastAPI
from fastapi.responses import JSONResponse
from core_config import get_settings
from core_logging import get_logger, log_stage
import httpx

settings = get_settings()
logger = get_logger("memory_api")
app = FastAPI(title="BatVault Memory API", version="0.1.0")

@app.on_event("startup")
async def bootstrap_arango():
    auth = (settings.arango_root_user, settings.arango_root_password)
    async with httpx.AsyncClient(base_url=settings.arango_url, auth=auth, timeout=20.0) as client:
        # Create database if not exists
        try:
            r = await client.post("/_api/database", json={"name": settings.arango_db})
            if r.status_code in (200, 201):
                log_stage(logger, "bootstrap", "arango_db_created", db=settings.arango_db)
            else:
                log_stage(logger, "bootstrap", "arango_db_create_skip_or_exists",
                          status=r.status_code, body=r.text[:200])
        except Exception as e:
            log_stage(logger, "bootstrap", "arango_db_create_error", error=str(e))

        base = f"/_db/{settings.arango_db}"

        # Ensure collections
        for cname, ctype in [("nodes","document"), ("edges","edge")]:
            try:
                r = await client.post(f"{base}/_api/collection",
                                      json={"name": cname, "type": 3 if ctype=="edge" else 2})
                log_stage(logger, "bootstrap", "arango_collection_ensure",
                          collection=cname, status=r.status_code)
            except Exception as e:
                log_stage(logger, "bootstrap", "arango_collection_error",
                          collection=cname, error=str(e))

        # Ensure vector index (best-effort)
        if settings.arango_vector_index_enabled:
            try:
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
                    "metric": settings.vector_metric,
                    "engine": settings.vector_engine,
                    "M": settings.hnsw_m,
                    "efConstruction": settings.hnsw_efconstruction
                }
                r = await client.post(f"{base}/_api/index?collection=nodes", json=payload)
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
    return {"ok": True, "service": "memory_api"}

@app.get("/readyz")
async def readyz():
    # Probe Arango for version to confirm readiness
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.arango_url}/_api/version")
            if r.status_code == 200:
                return {"ready": True}
    except Exception:
        pass
    return {"ready": False}

# ---- Stubs for Milestone 0 ----
@app.get("/api/schema/field-catalog")
def field_catalog():
    return {"fields": []}

@app.get("/api/schema/relation-catalog")
def relation_catalog():
    return {"relations": []}

@app.post("/api/resolve/text")
def resolve_text(payload: dict):
    return {"matches": [], "query": payload.get("q")}

@app.post("/api/graph/expand_candidates")
def expand_candidates(payload: dict):
    return {"anchor": payload.get("anchor"), "k": 1, "neighbors": []}

# ---- Enrich stub ----------------------------------------------------------
@app.get("/api/enrich/{node_type}/{node_id}")
def enrich_node(node_type: str, node_id: str):
    """Return a synthetic ‘enriched’ payload for Milestone 0 smoke/demo."""
    return {
        "type": node_type,
        "id": node_id,
        "summary": "(stub) enrichment not yet implemented",
        "embeddings": [],
    }
