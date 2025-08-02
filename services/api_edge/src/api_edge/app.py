import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
    PlainTextResponse,
    JSONResponse,
    Response
)
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from core_logging import get_logger, log_stage
import core_metrics
from core_config import get_settings, Settings
from core_utils import idempotency_key
from core_utils.ids import generate_request_id
from core_utils.health import attach_health_routes

import time, json, httpx, asyncio

settings: Settings = get_settings()
logger = get_logger("api_edge")
app = FastAPI(title="BatVault API Edge", version="0.1.0")

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:                         # pragma: no cover
    return Response(generate_latest(),
                    media_type=CONTENT_TYPE_LATEST)

# ───────────────────── CORS allow-list (Q-2) ──────────────────────
_origins: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

    # ─────────────────── Rate-limiting middleware (A-1) ───────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.api_rate_limit_default],
)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return PlainTextResponse("Too Many Requests", status_code=429)

# ---- Middleware: auth stub ----
@app.middleware("http")
async def auth_stub(request: Request, call_next):
    if settings.auth_disabled:
        request.state.auth = {"mode": "disabled"}
        return await call_next(request)
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
        log_stage(logger, "request", "request_start",
                  request_id=idem, path=request.url.path, method=request.method)
        response = await call_next(request)
        dt = int((time.perf_counter() - t0) * 1000)
        core_metrics.histogram("ttfb_ms", float(dt))
        response.headers["x-request-id"] = idem
        log_stage(logger, "request", "request_end",
                  request_id=idem, status_code=response.status_code,
                  latency_ms=dt)
        # ── Fallback counter -------------------------------------------- #
        try:
            from fastapi.responses import JSONResponse as _JSONResponse
            if isinstance(response, _JSONResponse):
                import orjson
                data = orjson.loads(response.body)
                if isinstance(data, dict) and data.get("meta", {}).get("fallback_used"):
                    core_metrics.counter("fallback_total", 1)
        except Exception:
            pass
        return response
    except Exception as e:
        log_stage(logger, "request", "request_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": "internal_error"})

async def check_gateway_ready() -> bool:
    """
    Returns True iff Gateway /readyz returns status: ready.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://gateway:8081/readyz")
        return r.status_code == 200 and r.json().get("status") == "ready"
    except Exception:
        return False

async def _readiness() -> dict:
    ready = await check_gateway_ready()
    return {
        "status": "ready" if ready else "degraded",
        "request_id": generate_request_id(),
    }

# — single, canonical wiring of /healthz + /readyz —
attach_health_routes(
    app,
    checks={
        "liveness": lambda: {"status": "ok"},
        "readiness": _readiness,
    },
)

# ---- Ops: ensure MinIO bucket via Gateway ----
@app.get("/ops/minio/bucket")
async def ensure_bucket():
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
    return StreamingResponse(eventgen(), media_type="text/event-stream")

@app.post("/v2/query")
async def v2_query_passthrough(request: Request):
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        payload = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post("http://gateway:8081/v2/query", json=payload)
        return JSONResponse(
            status_code=r.status_code,
            content=r.json(),
            headers={"x-snapshot-etag": r.headers.get("x-snapshot-etag", "")},
        )
