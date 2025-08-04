# services/ingest/src/ingest/app.py

from fastapi import FastAPI, Request
from core_logging import get_logger, log_stage
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_config import settings            # ← unified, validated settings
import core_metrics, time
import httpx
from fastapi.responses import JSONResponse, Response
import inspect
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST


app = FastAPI(title="BatVault Ingest", version="0.1.0")
logger = get_logger("ingest")
logger.propagate = True

# ── HTTP middleware: deterministic IDs, logs & TTFB histogram ──────────────
@app.middleware("http")
async def _request_logger(request: Request, call_next):
    idem = generate_request_id()
    t0   = time.perf_counter()
    log_stage(logger, "request", "request_start",
              request_id=idem, path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt_ms = int((time.perf_counter() - t0) * 1000)
    core_metrics.histogram("ingest_ttfb_ms", float(dt_ms))
    resp.headers["x-request-id"] = idem
    log_stage(logger, "request", "request_end",
              request_id=idem, status_code=resp.status_code,
              latency_ms=dt_ms)
    return resp

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:                         # pragma: no cover
    return Response(generate_latest(),
                    media_type=CONTENT_TYPE_LATEST)

async def _ping_gateway_ready() -> bool:
    """
    Returns True iff Gateway /readyz reports status “ready”.
    Kept async to allow monkey-patching with sync lambdas in unit-tests.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get("http://gateway:8081/readyz")
            return r.status_code == 200 and r.json().get("status") == "ready"
    except Exception:
        return False


async def _readiness() -> dict:
    """Composite readiness probe.

    • “starting”  → snapshot not yet loaded  
    • “ready”     → snapshot present **and** Gateway ready  
    • “degraded” → snapshot present but Gateway not ready

    ``_ping_gateway_ready`` may be **sync** or **async** (tests patch it with
    a plain lambda).  We therefore support both styles transparently.
    """
    etag = getattr(app.state, "snapshot_etag", None)
    if etag is None:
        return {
            "status": "starting",
            "snapshot_etag": None,
        }

    # tolerate both sync and async implementations
    maybe_coro = _ping_gateway_ready()
    if inspect.isawaitable(maybe_coro):
        mem_ok = await maybe_coro
    else:
        mem_ok = bool(maybe_coro)
    return {
        "status": "ready" if mem_ok else "degraded",
        "snapshot_etag": etag,
        "request_id": generate_request_id(),
    }


attach_health_routes(
    app,
    checks={
        # no liveness override → uses default {"ok": True}
        "readiness": _readiness,
    },
) 