# services/ingest/src/ingest/app.py

from fastapi import FastAPI, Request
from core_logging import get_logger, log_stage
from core_observability.otel import setup_tracing, instrument_fastapi_app
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_config import get_settings 
import core_metrics, time
from core_http.client import get_http_client
from core_config.constants import timeout_for_stage
import os
_INGEST_UPSTREAM_BASE = os.getenv("INGEST_UPSTREAM_BASE", "http://gateway:8081").rstrip("/")
from fastapi.responses import JSONResponse, Response
import inspect
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core_observability.otel import inject_trace_context

app = FastAPI(title="BatVault Ingest", version="0.1.0")

# Ensure OTEL middleware wraps all subsequent middlewares/handlers
instrument_fastapi_app(app, service_name=os.getenv('OTEL_SERVICE_NAME') or 'ingest')

logger = get_logger("ingest")
logger.propagate = False
setup_tracing(os.getenv("OTEL_SERVICE_NAME") or "ingest")

@app.middleware("http")
async def _request_logger(request: Request, call_next):
    idem = generate_request_id()
    # Observability: expose current trace/span IDs (should be non-zero if OTEL middleware wrapped us)
    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, 'trace_id', 0):
            log_stage(logger, 'observability', 'trace_ctx',
                      request_id=idem,
                      trace_id=f"{_ctx.trace_id:032x}",
                      span_id=f"{_ctx.span_id:016x}")
    except Exception:
        pass
    t0   = time.perf_counter()
    log_stage(logger, "request", "request_start",
              request_id=idem, path=str(request.url.path), method=request.method)

    resp = await call_next(request)

    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, 'trace_id', 0):
            resp.headers["x-trace-id"] = f"{_ctx.trace_id:032x}"
    except Exception:
        pass

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
        c = get_http_client(timeout_ms=int(1000*timeout_for_stage('enrich')))
        r = await c.get(
            f"{_INGEST_UPSTREAM_BASE}/readyz",
            headers=inject_trace_context({}),
        )
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