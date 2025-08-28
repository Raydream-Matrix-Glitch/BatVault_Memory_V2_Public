# services/ingest/src/ingest/app.py

from fastapi import FastAPI, Request
from core_logging import get_logger, log_stage
from core_observability.otel import init_tracing, instrument_fastapi_app
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_config import settings            # ← unified, validated settings
import core_metrics, time
import httpx
import os
from fastapi.responses import JSONResponse, Response
import inspect
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST


app = FastAPI(title="BatVault Ingest", version="0.1.0")
instrument_fastapi_app(app, service_name=os.getenv('OTEL_SERVICE_NAME') or 'ingest')
logger = get_logger("ingest")
logger.propagate = True
init_tracing(os.getenv("OTEL_SERVICE_NAME") or "ingest")

# ---- OTEL server span middleware (before request logger) -------------------
@app.middleware("http")
async def _otel_server_span(request: Request, call_next):
    try:
        from opentelemetry import trace as _trace  # type: ignore
        tracer = _trace.get_tracer(os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "ingest")
        name = f"HTTP {request.method} {request.url.path}"
        try:
            from opentelemetry.propagate import extract  # type: ignore
            _ctx_in = extract(dict(request.headers))
        except Exception:
            _ctx_in = None
        _cm = tracer.start_as_current_span(name, context=_ctx_in) if _ctx_in is not None else tracer.start_as_current_span(name)  # type: ignore
        with _cm as span:  # type: ignore
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.route", request.url.path)
            resp = await call_next(request)
            try:
                ctx = span.get_span_context()  # type: ignore[attr-defined]
                resp.headers["x-trace-id"] = f"{ctx.trace_id:032x}"
            except Exception:
                pass
            return resp
    except Exception:
        return await call_next(request)

# ── HTTP middleware: deterministic IDs, logs & TTFB histogram ──────────────
@app.middleware("http")
async def _request_logger(request: Request, call_next):
    idem = generate_request_id()
    t0   = time.perf_counter()
    log_stage(logger, "request", "request_start",
              request_id=idem, path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt_s = (time.perf_counter() - t0)
    core_metrics.histogram("ingest_ttfb_seconds", dt_s)
    resp.headers["x-request-id"] = idem
    try:
        core_metrics.counter("ingest_http_requests_total", 1, method=request.method, code=str(resp.status_code))
        if str(resp.status_code).startswith("5"):
            core_metrics.counter("ingest_http_5xx_total", 1)
    except Exception:
        pass
    log_stage(logger, "request", "request_end",
              request_id=idem, status_code=resp.status_code,
              latency_ms=dt_s * 1000.0)
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