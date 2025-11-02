from __future__ import annotations
import os
from .otel import init_tracing, instrument_fastapi_app
from core_logging.request_logging import attach_request_logging

def instrument_app(
    app,
    service_name: str | None = None,
    *,
    ttfb_label_route: bool = True,
    attach_metrics_endpoint: bool = True,
) -> None:
    """
    One-call, idempotent FastAPI instrumentation:
      • sets up OTEL tracer (prefers OTEL_* env), 
      • installs the server-span middleware, 
      • installs structured request logging with consistent metric prefixes.
    """
    svc = service_name or os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "batvault"
    # Tracing first so spans have non-zero ids for logging/metrics exemplars
    init_tracing(svc)
    instrument_fastapi_app(app, service_name=svc)
    # Standard, structured request logging + /metrics gauge prefixes
    attach_request_logging(app, service=svc, metric_prefix=svc, ttfb_label_route=ttfb_label_route)
    # Optionally expose Prometheus /metrics uniformly (no-ops if core_metrics absent)
    if attach_metrics_endpoint:
        try:
            from core_metrics.fastapi import attach_prometheus_endpoint  # type: ignore
            attach_prometheus_endpoint(app)
        except Exception:
            # metrics are optional in some deployments / tests
            pass