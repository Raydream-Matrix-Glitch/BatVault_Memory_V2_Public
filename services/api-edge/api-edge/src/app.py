from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from core_logging import get_logger, log_event
from core_config import get_settings, Settings
from core_utils import idempotency_key
import time
import json

settings: Settings = get_settings()
logger = get_logger("api-edge")

app = FastAPI(title="BatVault API Edge", version="0.1.0")

# ---- Middleware: auth stub ----
@app.middleware("http")
async def auth_stub(request: Request, call_next):
    if settings.auth_disabled:
        request.state.auth = {"mode": "disabled"}
        return await call_next(request)
    # placeholder for Bearer/JWT later
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        request.state.auth = {"mode": "bearer"}
        return await call_next(request)
    return JSONResponse(status_code=401, content={"error": "unauthorized"})

# ---- Middleware: request logging + ids ----
@app.middleware("http")
async def req_logger(request: Request, call_next):
    try:
        body = await request.body()
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            parsed = body.decode("utf-8", errors="ignore")
        idem = idempotency_key(request.headers.get("Idempotency-Key"),
                               request.url.path, dict(request.query_params), parsed)
        request.state.request_id = idem
        t0 = time.perf_counter()
        log_event(logger, "request_start",
                  request_id=idem, path=request.url.path, method=request.method)
        response = await call_next(request)
        dt = int((time.perf_counter() - t0) * 1000)
        response.headers["x-request-id"] = idem
        log_event(logger, "request_end",
                  request_id=idem, status_code=response.status_code, latency_ms=dt)
        return response
    except Exception as e:
        log_event(logger, "request_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": "internal_error"})

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "api-edge"}

@app.get("/readyz")
def readyz():
    return {"ready": True}

# ---- Ops: ensure MinIO bucket via Gateway ----
@app.get("/ops/minio/bucket")
async def ensure_bucket():
    # proxy to gateway which does the ensure
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post("http://gateway:8081/ops/minio/ensure-bucket")
        return JSONResponse(status_code=r.status_code, content=r.json())

# ---- SSE demo endpoint ----
@app.get("/stream/demo")
async def stream_demo():
    async def eventgen():
        for i in range(5):
            yield f"event: tick\ndata: {i}\n\n"
            await asyncio.sleep(0.5)
    import asyncio
    return StreamingResponse(eventgen(), media_type="text/event-stream")
