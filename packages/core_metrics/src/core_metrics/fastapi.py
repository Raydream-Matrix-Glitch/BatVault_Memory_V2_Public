from __future__ import annotations
from fastapi import FastAPI, Response
try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
except Exception:  # pragma: no cover - keeps unit tests happy without Prometheus installed
    generate_latest = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

def attach_prometheus_endpoint(app: FastAPI, path: str = "/metrics") -> None:
    """
    Attach a Prometheus scrape endpoint to *app* at *path* (default: "/metrics").
    Safe to call multiple times; subsequent calls are no-ops because the route
    is registered under a stable name.
    """
    route_name = f"core_metrics:{path}"
    for r in app.router.routes:
        if getattr(r, "name", None) == route_name:
            return  # already attached

    @app.get(path, include_in_schema=False, name=route_name)
    def _metrics() -> Response:
        if generate_latest is None:  # pragma: no cover
            return Response("# prometheus_client not installed\n", media_type=CONTENT_TYPE_LATEST)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)