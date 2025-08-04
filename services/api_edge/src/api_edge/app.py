from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Callable, Dict

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from api_edge.rate_limit import RateLimitMiddleware
from core_config import Settings, get_settings
from core_logging import get_logger, log_stage
import core_metrics
from core_utils.health import attach_health_routes
from core_utils import idempotency_key
from core_utils.ids import generate_request_id

# httpx->requests shim: expose `.iter_content()` for SSE streaming compatibility
if not hasattr(httpx.Response, "iter_content"):

    def _iter_content(self, chunk_size: int = 4096):  # noqa: D401, ANN001
        yield from self.iter_bytes()

    httpx.Response.iter_content = _iter_content  # type: ignore[attr-defined]

# ────────────────────────────────────────────────────────────────────────────
# 2. Settings, logging, application instance
# ────────────────────────────────────────────────────────────────────────────
settings: Settings = get_settings()
logger = get_logger("api_edge")
logger.propagate = True

app = FastAPI(title="BatVault API Edge", version="0.2.0")

# ────────────────────────────────────────────────────────────────────────────
# 3. Production-grade middlewares
# ────────────────────────────────────────────────────────────────────────────

# ---- 3.1 CORS --------------------------------------------------------------
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

# ---- 3.2 Lightweight token-bucket rate-limit -------------------------------
rate_def = settings.api_rate_limit_default or "5/second"
_m = re.match(r"(?P<count>\d+)/(?P<unit>second|minute|hour)", rate_def.strip())
_count = int(_m.group("count")) if _m else 5
_seconds = {"second": 1, "minute": 60, "hour": 3600}[_m.group("unit")] if _m else 1

app.add_middleware(
    RateLimitMiddleware,
    capacity=_count,
    refill_per_sec=_count / _seconds,
    # Ops paths that must never throttle; overridable via env
    exclude_paths=tuple(
        p.strip()
        for p in os.getenv(
            "API_RATE_LIMIT_EXCLUDE_PATHS", "/healthz,/readyz,/metrics"
        ).split(",")
        if p.strip()
    ),
)

# ---- 3.3 Auth stub ---------------------------------------------------------
@app.middleware("http")
async def auth_stub(request: Request, call_next):  # noqa: D401
    if settings.auth_disabled:
        request.state.auth = {"mode": "disabled"}
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        request.state.auth = {"mode": "bearer"}
        return await call_next(request)

    return JSONResponse(status_code=401, content={"error": "unauthorized"})

# ---- 3.4 Request logging & metrics -----------------------------------------
@app.middleware("http")
async def req_logger(request: Request, call_next):  # noqa: D401
    try:
        # ---- pre-request ----------------------------------------------------
        body = await request.body()
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            parsed = body.decode("utf-8", errors="ignore")

        idem = idempotency_key(
            request.headers.get("Idempotency-Key"),
            request.url.path,
            dict(request.query_params),
            parsed,
        )
        request.state.request_id = idem

        t0 = time.perf_counter()
        log_stage(
            logger,
            "request",
            "request_start",
            request_id=idem,
            path=request.url.path,
            method=request.method,
        )

        # ---- downstream route ----------------------------------------------
        response = await call_next(request)

        # ---- post-request metrics & logging ---------------------------------
        dt_ms = (time.perf_counter() - t0) * 1000.0

        core_metrics.histogram("ttfb_seconds", dt_ms / 1000.0)
        core_metrics.counter(
            "api_edge_http_requests_total",
            1,
            method=request.method,
            code=str(response.status_code),
        )

        response.headers["x-request-id"] = idem
        log_stage(
            logger,
            "request",
            "request_end",
            request_id=idem,
            status_code=response.status_code,
            latency_ms=dt_ms,
        )
        return response

    except Exception as e:  # pragma: no cover
        if isinstance(e, StarletteHTTPException):
            log_stage(
                logger,
                "request",
                "request_error",
                error=f"{e.status_code}:{e.detail}",
            )
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

        log_stage(logger, "request", "request_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": "internal_error"})

# ────────────────────────────────────────────────────────────────────────────
# 4. Metrics & health routes (prod-safe)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/metrics", include_in_schema=False)  # Prometheus scrape
def metrics() -> Response:  # pragma: no cover
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def check_gateway_ready() -> bool:
    """Returns True iff Gateway /readyz returns status: ready."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://gateway:8081/readyz")
        return r.status_code == 200 and r.json().get("status") == "ready"
    except Exception:
        return False


async def _readiness() -> dict:
    ready = await check_gateway_ready()
    return {"status": "ready" if ready else "degraded", "request_id": generate_request_id()}


# canonical wiring of /healthz + /readyz
attach_health_routes(
    app,
    checks={
        "liveness": lambda: {"status": "ok"},
        "readiness": _readiness,
    },
)

# ────────────────────────────────────────────────────────────────────────────
# 5. Ops / integration routes (prod-safe)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/ops/minio/bucket", include_in_schema=False)
async def ensure_bucket():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post("http://gateway:8081/ops/minio/ensure-bucket")
        return JSONResponse(status_code=r.status_code, content=r.json())

# ────────────────────────────────────────────────────────────────────────────
# 6. Dev / test-only helpers
# ────────────────────────────────────────────────────────────────────────────
if settings.environment in {"dev", "test"}:

    @app.get("/ratelimit-test", include_in_schema=False)
    async def _ratelimit_test() -> PlainTextResponse:  # noqa: D401
        """Zero-latency endpoint exclusively for rate-limit unit-tests."""
        return PlainTextResponse("ok")

    @app.get("/stream/demo", include_in_schema=False)
    async def stream_demo():
        """Short server-sent-events demo (5 ticks)."""
        async def _eventgen():
            for i in range(5):
                yield f"event: tick\ndata: {i}\n\n".encode()
                await asyncio.sleep(0.5)

        return StreamingResponse(_eventgen(), media_type="text/event-stream")

# ────────────────────────────────────────────────────────────────────────────
# Startup metric priming (names must exist before first scrape)
# ────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def _prime_metrics() -> None:  # noqa: D401
    try:
        core_metrics.gauge("api_edge_ttfb_seconds", 0.0)
        core_metrics.counter("api_edge_fallback_total", 0)
    except Exception:
        pass