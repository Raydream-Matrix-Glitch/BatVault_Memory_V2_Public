import os
import httpx
import inspect
from fastapi import FastAPI, Request
from core_logging import get_logger, log_stage
from core_utils.fastapi_bootstrap import setup_service
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_config import get_settings 
from core_http.client import get_http_client
from core_config.constants import timeout_for_stage
from core_observability.otel import inject_trace_context

app = FastAPI(title="BatVault Ingest", version="0.1.0")
_INGEST_UPSTREAM_BASE = os.getenv("INGEST_UPSTREAM_BASE", "http://gateway:8081").rstrip("/")
setup_service(app, 'ingest')

logger = get_logger("ingest")

@app.on_event("startup")
async def _log_effective_sensitivity_order() -> None:
    """
    Emit the effective SENSITIVITY_ORDER once at startup for ops clarity.
    Looks for (in order): settings.SENSITIVITY_ORDER | settings.sensitivity_order |
    env SENSITIVITY_ORDER (comma-separated) | conservative default.
    """
    try:
        cfg = get_settings()
        order = None
        source = None
        for attr in ("SENSITIVITY_ORDER", "sensitivity_order", "SENSITIVITY_LEVELS", "sensitivity_levels"):
            if hasattr(cfg, attr):
                val = getattr(cfg, attr)
                if isinstance(val, (list, tuple)):
                    order = list(val)
                    source = f"settings.{attr}"
                    break
        if order is None:
            raw = os.getenv("SENSITIVITY_ORDER")
            if raw:
                order = [x.strip() for x in raw.split(",") if x.strip()]
                source = "env.SENSITIVITY_ORDER"
        if order is None:
            # conservative default (least→most restrictive)
            order = ["public", "internal", "confidential", "secret"]
            source = "default"
        log_stage(
            logger, "ingest", "sensitivity_order",
            order=order, source=source, request_id=generate_request_id()
        )
    except (AttributeError, TypeError, ValueError, RuntimeError) as e:
        # Narrow catch and surface the failure; do not crash the service
        log_stage(
            logger, "ingest", "sensitivity_order_log_failed",
            error=str(e), request_id=generate_request_id()
        )

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
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
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
        # Surface gateway ping failures in readiness logs for easier triage
        try:
            log_stage(
                logger, "readiness", "gateway_ping_failed",
                error=str(e), request_id=generate_request_id()
            )
        except (RuntimeError, ValueError): pass
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
            # Not ready until a snapshot is loaded
            "ready": False,
            "snapshot_etag": None,
        }

    # tolerate both sync and async implementations
    maybe_coro = _ping_gateway_ready()
    if inspect.isawaitable(maybe_coro):
        mem_ok = await maybe_coro
    else:
        mem_ok = bool(maybe_coro)
    # Storage readiness (fail-closed if Arango unreachable in non-DEV)
    storage_ok = True
    try:
        from core_storage import ArangoStore
        ArangoStore(lazy=False)  # force connect
    except (RuntimeError, OSError, ValueError) as e:
        storage_ok = False
        try:
            log_stage(
                logger, "readiness", "storage_unavailable",
                error=str(e), request_id=generate_request_id()
            )
        except (RuntimeError, ValueError):
            pass
    overall_ok = bool(mem_ok and storage_ok)
    return {
        "status": "ready" if overall_ok else "degraded",
        # Canonical readiness boolean required by Baseline
        # (fail-closed on storage outages outside DEV; this logic remains closed by default)
        "ready": overall_ok,
        "snapshot_etag": etag,
        "request_id": generate_request_id(),
        "storage_ok": storage_ok,
    }

attach_health_routes(
    app,
    checks={
        # no liveness override → uses default {"ok": True}
        "readiness": _readiness,
    },
) 