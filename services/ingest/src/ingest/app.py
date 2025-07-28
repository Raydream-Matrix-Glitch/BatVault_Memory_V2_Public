from fastapi import FastAPI
from core_logging import get_logger
app = FastAPI(title="BatVault Ingest", version="0.1.0")
logger = get_logger("ingest")

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "ingest"}
