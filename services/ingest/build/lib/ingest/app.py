from fastapi import FastAPI
from core_logging import get_logger, log_stage
import httpx

app = FastAPI(title="BatVault Ingest", version="0.1.0")
logger = get_logger("ingest")

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "ingest"}

@app.get("/readyz")
async def readyz():
    # Probe Memory API as a backing dependency
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://memory_api:8082/healthz")
            if r.status_code == 200:
                return {"ready": True}
    except Exception as e:
        pass
    return {"ready": False}
