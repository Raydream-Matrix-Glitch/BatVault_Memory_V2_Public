from __future__ import annotations
import os
from typing import Dict, Optional

def init_tracing(service_name: Optional[str] = None) -> None:
    """
    Idempotent OTEL bootstrap. Honors OTEL_* env vars; falls back to sane defaults.
    Safe to call even when opentelemetry packages are not installed.
    """
    try:
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, OTLPSpanExporter  # type: ignore
        from opentelemetry import trace as _trace  # type: ignore
        svc = service_name or os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "batvault"
        res = Resource.create({"service.name": svc})
        # Avoid double init
        if not isinstance(_trace.get_tracer_provider(), TracerProvider):
            tp = TracerProvider(resource=res)
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            _trace.set_tracer_provider(tp)
    except Exception:
        # Optional dependency – fail silent
        pass

def instrument_fastapi_app(app, service_name: Optional[str] = None) -> None:
    """
    Adds an HTTP middleware that starts a server span for each request,
    propagates context to responses via `x-trace-id`, and sets common attributes.
    """
    init_tracing(service_name)
    try:
        from opentelemetry import trace as _trace  # type: ignore
    except Exception:
        _trace = None  # type: ignore

    @app.middleware("http")
    async def _otel_server_span(request, call_next):
        tracer = _trace.get_tracer(service_name or os.getenv("OTEL_SERVICE_NAME") or "batvault") if _trace else None
        name = f"HTTP {getattr(request, 'method', 'GET')} {getattr(request.url, 'path', '/')}"
        if tracer:
            try:
                from opentelemetry.propagate import extract  # type: ignore
                ctx_in = extract(dict(getattr(request, "headers", {})))
            except Exception:
                ctx_in = None
            # start the server span with upstream context (if any)
            if ctx_in is not None:
                cm = tracer.start_as_current_span(name, context=ctx_in)  # type: ignore
            else:
                cm = tracer.start_as_current_span(name)  # type: ignore
            with cm as span:  # type: ignore
                try:
                    span.set_attribute("http.method", getattr(request, "method", "GET"))
                    span.set_attribute("http.route", getattr(getattr(request, "url", None), "path", "/"))
                except Exception:
                    pass
                response = await call_next(request)
                try:
                    ctx = span.get_span_context()  # type: ignore[attr-defined]
                    response.headers["x-trace-id"] = f"{ctx.trace_id:032x}"
                except Exception:
                    pass
                return response
        # if tracer missing
        response = await call_next(request)
        return response

def current_trace_id_hex() -> Optional[str]:
    try:
        from opentelemetry import trace as _t  # type: ignore
        span = _t.get_current_span()
        if span:
            ctx = span.get_span_context()  # type: ignore[attr-defined]
            if getattr(ctx, "trace_id", 0):
                return f"{ctx.trace_id:032x}"
    except Exception:
        pass
    return None

def inject_trace_context(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Returns a copy of headers with W3C trace context injected when available.
    Safe no-op when OTEL is absent.
    """
    hdrs = dict(headers or {})
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(hdrs)  # mutates in place
    except Exception:
        pass
    return hdrs
