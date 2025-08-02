# services/ingest/src/ingest/app.py

from fastapi import FastAPI
from core_logging import get_logger
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
import httpx
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

app = FastAPI(title="BatVault Ingest", version="0.1.0")
logger = get_logger("ingest")

# ── Prometheus scrape endpoint (CI + Prometheus) ───────────────────────────
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:                         # pragma: no cover
    return Response(generate_latest(),
                    media_type=CONTENT_TYPE_LATEST)

async def _ping_memory_api() -> bool:
    """
    Returns True iff Memory-API /readyz responds 200 + {"status":"ready"}.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://memory_api:8082/readyz")
    except Exception:
        # network error or timeout
        return False

    return (
        r.status_code == 200
        and r.json().get("status") == "ready"
    )


async def _readiness() -> dict:
    """
    - Stay in "starting" until snapshot_etag is set on app.state
    - Once snapshot_etag exists, ping Memory-API before flipping to "ready"
    """
    etag = getattr(app.state, "snapshot_etag", None)
    if etag is None:
        return {
            "status": "starting",
            "snapshot_etag": None,
        }

    mem_ok = await _ping_memory_api()
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