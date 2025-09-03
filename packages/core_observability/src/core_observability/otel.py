import os
import re
import hashlib
from typing import Dict, Optional

def init_tracing(service_name: Optional[str] = None) -> None:
    """
    Idempotent OTEL bootstrap. Honors OTEL_* env vars; falls back to sane defaults.
    Safe to call even when opentelemetry packages are not installed.
    """
    try:
        from opentelemetry import trace as _trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.sampling import ParentBased, AlwaysOnSampler  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, OTLPSpanExporter  # type: ignore

        svc = service_name or os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "batvault"
        res = Resource.create({"service.name": svc})
        tp = TracerProvider(resource=res, sampler=ParentBased(AlwaysOnSampler()))

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))

        # Install the provider **unconditionally** so spans have real, non-zero IDs
        _trace.set_tracer_provider(tp)

        # Prefer W3C TraceContext globally (explicit is better than implicit)
        try:
            from opentelemetry.propagate import set_global_textmap  # type: ignore
            from opentelemetry.propagators.tracecontext import TraceContextTextMapPropagator  # type: ignore
            set_global_textmap(TraceContextTextMapPropagator())
        except Exception:
            pass
    except Exception:
        # Optional dependency – fail silent
        pass

_TRACEPARENT_RE = re.compile(r"^\s*[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}\s*$", re.I)
_XTRACEID_RE = re.compile(r"^[0-9a-fA-F]{32}$")

def _parse_traceparent(val: Optional[str]) -> Optional[tuple[str, str]]:
    if not val:
        return None
    m = _TRACEPARENT_RE.match(val)
    return (m.group(1).lower(), m.group(2).lower()) if m else None

def instrument_fastapi_app(app, service_name: Optional[str] = None) -> None:
    """
    Adds an HTTP middleware that starts a server span for each request,
    propagates context to responses via `x-trace-id`, and sets common attributes.
    # Idempotency: avoid double-installing middleware in tests/reloads
    """
    if getattr(app, '_otel_server_span_installed', False):
        return
    setattr(app, '_otel_server_span_installed', True)
    init_tracing(service_name)
    # One-shot bootstrap log to make tracing state visible in environments where OTEL might be missing.
    try:
        from core_logging import get_logger, log_stage  # lazy import – avoid hard dep at import time
        _ob_logger = get_logger(service_name or os.getenv("OTEL_SERVICE_NAME") or "app")
        if not getattr(app, "_otel_boot_logged", False):
            setattr(app, "_otel_boot_logged", True)
            _has_otel = True
            try:
                from opentelemetry import trace as _probe  # type: ignore
                _ = _probe.get_tracer_provider()
            except Exception:
                _has_otel = False
            log_stage(_ob_logger, "observability", "tracing_setup",
                      otel_present=_has_otel,
                      exporter=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "",
                      propagators=os.getenv("OTEL_PROPAGATORS") or "default")
    except Exception:
        pass
    try:
        from opentelemetry import trace as _trace  # type: ignore
    except Exception:
        _trace = None  # type: ignore

    @app.middleware("http")
    async def _otel_server_span(request, call_next):
        tracer = _trace.get_tracer(service_name or os.getenv("OTEL_SERVICE_NAME") or "batvault") if _trace else None
        name = f"HTTP {getattr(request, 'method', 'GET')} {getattr(request.url, 'path', '/')}"
        # 1) Seed logging context from upstream traceparent (works even if OTEL SDK is absent).
        try:
            from core_logging import bind_trace_ids  # lazy import to avoid hard dep at import time
        except Exception:
            bind_trace_ids = None  # type: ignore
        ids = None
        try:
            hdrs = dict(getattr(request, "headers", {}))
            # Prefer upstream x-trace-id for deterministic correlation when OTEL is inactive
            x_tid = hdrs.get("x-trace-id") or hdrs.get("X-Trace-Id")
            if x_tid and _XTRACEID_RE.match(x_tid):
                ids = (x_tid.lower(), x_tid[:16].lower())
            else:
                ids = _parse_traceparent(hdrs.get("traceparent") or hdrs.get("Traceparent"))
            if ids and bind_trace_ids:
                bind_trace_ids(*ids)  # early bind so first logs see a trace id
        except Exception:
            pass
        synthetic_tid: Optional[str] = None
        if tracer:
            try:
                from opentelemetry.propagate import extract  # type: ignore
                ctx_in = extract(dict(getattr(request, "headers", {})))
            except Exception:
                ctx_in = None
            # 2) Start the server span with upstream context (if any)
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
                # 3) Re-bind with the *real* (non-zero) span ids now that the span is active.
                try:
                    if bind_trace_ids:
                        ctx = span.get_span_context()  # type: ignore[attr-defined]
                        if getattr(ctx, "trace_id", 0):
                            bind_trace_ids(f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}")
                        else:
                            # OTEL present but span is non-recording (id == 0).
                            # Prefer upstream x-trace-id; synthesize only if missing.
                            hdrs = dict(getattr(request, "headers", {}))
                            x_tid = hdrs.get("x-trace-id") or hdrs.get("X-Trace-Id")
                            req_id = None
                            if x_tid and _XTRACEID_RE.match(x_tid):
                                synthetic_tid = x_tid.lower()
                            else:
                                try:
                                    from core_utils.ids import compute_request_id  # type: ignore
                                    req_id = compute_request_id(
                                        getattr(getattr(request, "url", None), "path", "/"),
                                        dict(getattr(request, "query_params", {})),
                                        None,
                                    )
                                    synthetic_tid = hashlib.blake2b(req_id.encode("utf-8"), digest_size=16).hexdigest()
                                except Exception:
                                    # last-resort stable id
                                    synthetic_tid = hashlib.blake2b(repr(request).encode("utf-8"), digest_size=16).hexdigest()
                            bind_trace_ids(synthetic_tid, synthetic_tid[:16])
                            # structured breadcrumb for the audit drawer (always log on invalid_otel_span)

                except Exception:
                    pass
                response = await call_next(request)
                # Always surface an x-trace-id for audit correlation (real → upstream → synthetic)
                try:
                    from core_observability.otel import current_trace_id_hex as _cur_tid  # self import ok
                except Exception:
                    _cur_tid = None  # type: ignore
                try:
                    ctx = span.get_span_context()  # type: ignore[attr-defined]
                except Exception:
                    ctx = None  # type: ignore
                try:
                    _tid = None
                    if ctx and getattr(ctx, "trace_id", 0):
                        _tid = f"{ctx.trace_id:032x}"
                    if not _tid and isinstance(ids, tuple):
                        _tid = ids[0]
                    if not _tid:
                        _tid = synthetic_tid
                    if not _tid and _cur_tid:
                        _tid = _cur_tid()
                    if _tid:
                        response.headers["x-trace-id"] = _tid
                except Exception:
                    pass
                # 4) Clear bound ids (avoid leakage across requests in worker reuse)
                try:
                    if bind_trace_ids:
                        bind_trace_ids(None, None)
                except Exception:
                    pass
                return response
        # if tracer missing – generate deterministic correlation IDs for logs
        try:
            hdrs = dict(getattr(request, "headers", {}))
            x_tid = hdrs.get("x-trace-id") or hdrs.get("X-Trace-Id")
            if x_tid and _XTRACEID_RE.match(x_tid):
                synthetic_tid = x_tid.lower()
                req_id = None
            else:
                from core_utils.ids import compute_request_id  # type: ignore
                req_id = compute_request_id(
                    getattr(getattr(request, "url", None), "path", "/"),
                    dict(getattr(request, "query_params", {})),
                    None,
                )
                synthetic_tid = hashlib.blake2b(req_id.encode("utf-8"), digest_size=16).hexdigest()
            if bind_trace_ids:
                bind_trace_ids(synthetic_tid, synthetic_tid[:16])
            try:
                from core_logging import get_logger, log_stage
                _ob_logger = get_logger(service_name or os.getenv("OTEL_SERVICE_NAME") or "app")
                log_stage(_ob_logger, "observability", "trace_fallback_synthetic",
                          reason="otel_sdk_absent",
                          request_id=req_id,
                          trace_id=synthetic_tid,
                          used_upstream=bool(x_tid and _XTRACEID_RE.match(x_tid)))
            except Exception:
                pass
        except Exception:
            pass
        response = await call_next(request)
        # Always include x-trace-id in the response when tracer is absent
        try:
            if synthetic_tid:
                response.headers["x-trace-id"] = synthetic_tid
        except Exception:
            pass
        try:
            if bind_trace_ids:
                bind_trace_ids(None, None)
        except Exception:
            pass
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
    Returns headers with W3C trace context (when available) and *always* sets `x-trace-id`
    so cross-service correlation works even when OTEL is inactive.
    """
    hdrs = dict(headers or {})
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(hdrs)  # mutates in place
        if not any(k.lower() == "traceparent" for k in hdrs.keys()):
            raise RuntimeError("no_otlp_context")
    except Exception:
        try:
            from core_logging import current_trace_ids
            tid, sid = current_trace_ids()
            if tid and sid and not any(k.lower() == "traceparent" for k in hdrs.keys()):
                hdrs["traceparent"] = f"00-{tid}-{sid}-01"
        except Exception:
            pass
    # Always propagate x-trace-id
    _tid = None
    try:
        _tid = current_trace_id_hex()
    except Exception:
        _tid = None
    if not _tid:
        try:
            from core_logging import current_trace_ids
            _tid, _sid = current_trace_ids()
        except Exception:
            _tid = None
    if _tid and not any(k.lower() == "x-trace-id" for k in hdrs.keys()):
        hdrs["x-trace-id"] = _tid
    return hdrs

# ---------------------------------------------------------------------------
# Unified tracing setup
# ---------------------------------------------------------------------------
_tracing_setup_done: bool = False

def _normalize_http_endpoint(ep: str) -> str:
    # Ensure HTTP exporter endpoints include the '/v1/traces' suffix.
    try:
        if ep.endswith("/v1/traces"):
            return ep
        # Strip trailing slashes and append standard path
        return ep.rstrip("/") + "/v1/traces"
    except Exception:
        return ep

def setup_tracing(service_name: str) -> None:
    """
    Configure a global OpenTelemetry tracer provider with HTTP OTLP exporter.

    This helper is idempotent: repeated calls do nothing after the first.
    It builds a Resource containing service.name and optional deployment.environment,
    installs a TracerProvider with a BatchSpanProcessor and OTLP HTTP exporter,
    registers it globally, and emits a one‑time structured log line indicating
    whether OTEL is active and which protocol/endpoint is used.

    If the OTLP exporter cannot be imported or created, a provider is still
    registered (with no exporter) so that spans acquire non‑zero IDs.  In that
    case the emitted log line reports ``otel_present`` as ``false``.
    """
    global _tracing_setup_done
    if _tracing_setup_done:
        return
    _tracing_setup_done = True
    otel_present = False
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    # Build the base provider and attempt to attach an HTTP exporter
    try:
        from opentelemetry import trace as _trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
        # Import the HTTP OTLP exporter; may fail if dependency is missing
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                OTLPSpanExporter,
            )
        except Exception:
            OTLPSpanExporter = None  # type: ignore

        # Build resource with service name and optional deployment environment
        attrs = {"service.name": service_name}
        try:
            env = (
                os.getenv("DEPLOYMENT_ENVIRONMENT")
                or os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT")
                or os.getenv("DEPLOYMENT")
                or None
            )
            if env:
                attrs["deployment.environment"] = env
        except Exception:
            pass
        res = Resource.create(attrs)
        tp = TracerProvider(resource=res)
        # Attempt to construct and attach the exporter
        if OTLPSpanExporter is not None:
            try:
                exporter = OTLPSpanExporter(endpoint=_normalize_http_endpoint(endpoint))
                tp.add_span_processor(BatchSpanProcessor(exporter))
                otel_present = True
            except Exception:
                # Failed to instantiate exporter; proceed without exporting spans
                otel_present = False
        # Register the provider globally regardless of exporter availability
        try:
            _trace.set_tracer_provider(tp)
            # Prefer W3C trace context propagation globally
            try:
                from opentelemetry.propagate import set_global_textmap  # type: ignore
                from opentelemetry.propagators.tracecontext import (  # type: ignore
                    TraceContextTextMapPropagator,
                )
                set_global_textmap(TraceContextTextMapPropagator())
            except Exception:
                pass
        except Exception:
            # Could not register provider; do not blow up
            pass
    except Exception:
        # opentelemetry isn't installed; nothing more to do
        otel_present = False
    # Emit a one‑shot observability log indicating tracing status
    try:
        from core_logging import get_logger, log_stage  # type: ignore
        _ob_logger = get_logger(service_name or os.getenv("OTEL_SERVICE_NAME") or "app")
        log_stage(
            _ob_logger,
            "observability",
            "tracing_setup",
            otel_present=otel_present,
            protocol="http",
            endpoint=endpoint,
        )
    except Exception:
        pass
