from __future__ import annotations
import time
from typing import Tuple
from fastapi import FastAPI, Request
from core_logging import (
    get_logger, log_stage, bind_trace_ids, bind_request_id, current_trace_ids,
    emit_request_summary, emit_request_error_summary
)
from core_utils.ids import generate_request_id
import core_metrics

_DEFAULT_SUPPRESS: Tuple[str, ...] = ("/health", "/healthz", "/ready", "/readyz", "/metrics")

def attach_request_logging(
    app: FastAPI,
    *,
    service: str,
    metric_prefix: str,
    ttfb_label_route: bool = False,
    suppress_paths: Tuple[str, ...] = _DEFAULT_SUPPRESS,
) -> None:
    """
    Install a uniform request logger middleware with health/metrics filtering.
    Emits:
      - {metric_prefix}_ttfb_seconds (histogram)
      - {metric_prefix}_http_requests_total (counter{method,code})
      - {metric_prefix}_http_5xx_total (counter)
    Adds headers:
      - x-request-id, x-trace-id (when available)
    """
    logger = get_logger(service)

    @app.middleware("http")
    async def _request_logger(request: Request, call_next):
        path = str(request.url.path or "")
        should_log = not any(path.endswith(p) for p in suppress_paths)

        # Bind/advertise current OTEL trace (best effort, no broad except)
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        if _ctx and getattr(_ctx, "trace_id", 0):
            bind_trace_ids(f"{_ctx.trace_id:032x}", f"{_ctx.span_id:016x}")

        # Preserve incoming request id when provided; generate otherwise.
        req_id = (
            request.headers.get("x-request-id")
            or request.headers.get("X-Request-Id")
            or generate_request_id()
        )
        bind_request_id(req_id)
        t0 = time.perf_counter()
        if should_log:
            log_stage(
                logger, "http.server", "http.server.request",
                request_id=req_id,
                http={"method": request.method, "target": path},
            )

        resp = await call_next(request)

        # Bubble trace id to clients for audit drawers (guard missing OTEL only)
        try:
            from opentelemetry import trace as _trace  # type: ignore
        except ImportError:
            _trace = None  # type: ignore
        if _trace is not None:
            _sp = _trace.get_current_span()
            _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
            if _ctx and getattr(_ctx, "trace_id", 0):
                resp.headers["x-trace-id"] = f"{_ctx.trace_id:032x}"
        # Fallback if OTEL is inactive: surface our context-bound trace id.
        if "x-trace-id" not in resp.headers:
            _tid, _ = current_trace_ids()
            if _tid:
                resp.headers["x-trace-id"] = _tid
        resp.headers["x-request-id"] = req_id

        dt = time.perf_counter() - t0
        # Optional route label for more granular TTFB panels (e.g. /v2/query)
        if ttfb_label_route:
            _route_obj = request.scope.get("route")
            _route = getattr(_route_obj, "path", None) or getattr(_route_obj, "path_format", None) or path
            core_metrics.histogram(f"{metric_prefix}_ttfb_seconds", dt, route=_route)
        else:
            core_metrics.histogram(f"{metric_prefix}_ttfb_seconds", dt)
        core_metrics.counter(f"{metric_prefix}_http_requests_total", 1, method=request.method, code=str(resp.status_code))
        if str(resp.status_code).startswith("5"):
            core_metrics.counter(f"{metric_prefix}_http_5xx_total", 1)

        if should_log:
            log_stage(
                logger, "http.server", "http.server.response",
                request_id=req_id,
                http={"status_code": resp.status_code, "method": request.method, "target": path},
                latency_ms=int(dt * 1000.0),
            )
            # Header-sourced fingerprints for deterministic correlation (no body peeking)
            # Minimal guard: only touch headers if present; never raise.
            _hdrs = getattr(resp, "headers", {})
            if _hdrs:
                _lower = {str(k).lower(): v for k, v in _hdrs.items()}
                log_stage(
                    logger, "summary", "response_headers",
                    snapshot_etag=_lower.get("x-snapshot-etag") or _lower.get("x-bv-snapshot-etag"),
                    policy_fp=_lower.get("x-bv-policy-fingerprint"),
                    allowed_ids_fp=_lower.get("x-bv-allowed-ids-fp"),
                    graph_fp=_lower.get("x-bv-graph-fp"),
                    bundle_fp=_lower.get("x-bundle-fp") or _lower.get("x-bv-bundle-fp"),
                    request_id=req_id,
                    )
            # Compact rollups
            emit_request_error_summary(logger, service=service)
            emit_request_summary(logger, service=service)
        return resp