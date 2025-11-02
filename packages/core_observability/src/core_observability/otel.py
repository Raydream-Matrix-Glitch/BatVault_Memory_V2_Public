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
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore
    except ImportError:
        # Optional dependency not installed – no-op initialization
        return

    svc = service_name or os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "batvault"
    res = Resource.create({"service.name": svc})
    tp = TracerProvider(resource=res, sampler=ParentBased(AlwaysOnSampler()))

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))

    # Install the provider so spans have real, non-zero IDs
    _trace.set_tracer_provider(tp)

    # Prefer W3C TraceContext globally (explicit is better than implicit)
    try:
        from opentelemetry.propagate import set_global_textmap  # type: ignore
        from opentelemetry.propagators.tracecontext import TraceContextTextMapPropagator  # type: ignore
        set_global_textmap(TraceContextTextMapPropagator())
    except ImportError:
        # Propagator extras not present – acceptable
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
        from core_logging import get_logger, log_stage  # type: ignore
        _ob_logger = get_logger(service_name or os.getenv("OTEL_SERVICE_NAME") or "app")
        if not getattr(app, "_otel_boot_logged", False):
            setattr(app, "_otel_boot_logged", True)
            try:
                from opentelemetry import trace as _probe  # type: ignore
                _ = _probe.get_tracer_provider()
                _has_otel = True
            except ImportError:
                _has_otel = False
            log_stage(
                _ob_logger,
                "observability",
                "tracing_setup",
                otel_present=_has_otel,
                exporter=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "",
                propagators=os.getenv("OTEL_PROPAGATORS") or "default",
                request_id="startup",
            )
    except ImportError:
        _ob_logger = None  # type: ignore
    try:
        from opentelemetry import trace as _trace  # type: ignore
    except ImportError:
        _trace = None  # type: ignore

    @app.middleware("http")
    async def _otel_server_span(request, call_next):
        tracer = _trace.get_tracer(service_name or os.getenv("OTEL_SERVICE_NAME") or "batvault") if _trace else None
        name = f"HTTP {getattr(request, 'method', 'GET')} {getattr(request.url, 'path', '/')}"
        # 1) Seed logging context from upstream traceparent (works even if OTEL SDK is absent).
        try:
            from core_logging import bind_trace_ids  # type: ignore
        except ImportError:
            bind_trace_ids = None  # type: ignore
        hdrs = getattr(request, "headers", {}) or {}
        # Prefer upstream x-trace-id for deterministic correlation when OTEL is inactive
        x_tid = hdrs.get("x-trace-id")
        ids = (x_tid.lower(), x_tid[:16].lower()) if (x_tid and _XTRACEID_RE.match(x_tid)) else _parse_traceparent(hdrs.get("traceparent"))
        if ids and bind_trace_ids:
            bind_trace_ids(*ids)  # early bind so first logs see a trace id
        synthetic_tid: Optional[str] = None
        if tracer:
            try:
                from opentelemetry.propagate import extract  # type: ignore
                ctx_in = extract(dict(hdrs))
            except ImportError:
                ctx_in = None
            # 2) Start the server span with upstream context (if any)
            if ctx_in is not None:
                cm = tracer.start_as_current_span(name, context=ctx_in)  # type: ignore
            else:
                cm = tracer.start_as_current_span(name)  # type: ignore
            with cm as span:  # type: ignore
                span.set_attribute("http.method", getattr(request, "method", "GET"))
                span.set_attribute("http.route", getattr(getattr(request, "url", None), "path", "/"))
                # 3) Re-bind with the *real* (non-zero) span ids now that the span is active.
                if bind_trace_ids:
                    ctx = span.get_span_context()  # type: ignore[attr-defined]
                    if getattr(ctx, "trace_id", 0):
                        bind_trace_ids(f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}")
                    else:
                        # OTEL present but span is non-recording (id == 0).
                        # Prefer upstream x-trace-id; synthesize only if missing.
                        if x_tid and _XTRACEID_RE.match(x_tid):
                            synthetic_tid = x_tid.lower()
                        else:
                            try:
                                from core_utils.ids import compute_request_id  # type: ignore
                                req_id = compute_request_id(
                                    getattr(getattr(request, "url", None), "path", "/"),
                                    getattr(getattr(request, "url", None), "query", ""),
                                    None,
                                )
                                synthetic_tid = hashlib.blake2b(req_id.encode("utf-8"), digest_size=16).hexdigest()
                            except ImportError:
                                # last-resort stable id
                                synthetic_tid = hashlib.blake2b(repr(request).encode("utf-8"), digest_size=16).hexdigest()
                        # Do not spoof span_id in fallback: keep summaries trace-only.
                        bind_trace_ids(synthetic_tid, None)
                response = await call_next(request)
                # Always surface an x-trace-id for audit correlation (real → upstream → synthetic)
                from core_observability.otel import current_trace_id_hex as _cur_tid  # type: ignore
                ctx = span.get_span_context()  # type: ignore[attr-defined]
                _tid = None
                if getattr(ctx, "trace_id", 0):
                    _tid = f"{ctx.trace_id:032x}"
                if not _tid and isinstance(ids, tuple):
                    _tid = ids[0]
                if not _tid:
                    _tid = synthetic_tid
                if not _tid and _cur_tid:
                    _tid = _cur_tid()
                if _tid:
                    response.headers["x-trace-id"] = _tid
                # 4) Clear bound ids (avoid leakage across requests in worker reuse)
                if bind_trace_ids:
                    bind_trace_ids(None, None)
                return response
        # if tracer missing – generate deterministic correlation IDs for logs
        if x_tid and _XTRACEID_RE.match(x_tid):
            synthetic_tid = x_tid.lower()
            req_id = None
        else:
            try:
                from core_utils.ids import compute_request_id  # type: ignore
                req_id = compute_request_id(
                    getattr(getattr(request, "url", None), "path", "/"),
                    getattr(getattr(request, "url", None), "query", ""),
                    None,
                )
                synthetic_tid = hashlib.blake2b(req_id.encode("utf-8"), digest_size=16).hexdigest()
            except ImportError:
                req_id = None
                synthetic_tid = hashlib.blake2b(repr(request).encode("utf-8"), digest_size=16).hexdigest()
        if bind_trace_ids:
            bind_trace_ids(synthetic_tid, synthetic_tid[:16])
        try:
            from core_logging import get_logger, log_stage  # type: ignore
            _ob_logger = _ob_logger or get_logger(service_name or os.getenv("OTEL_SERVICE_NAME") or "app")
            log_stage(
                _ob_logger,
                "observability",
                "trace_fallback_synthetic",
                reason="otel_sdk_absent",
                request_id=req_id,
                trace_id=synthetic_tid,
                used_upstream=bool(x_tid and _XTRACEID_RE.match(x_tid)),
            )
        except ImportError:
            pass
        response = await call_next(request)
        # Always include x-trace-id in the response when tracer is absent
        if synthetic_tid:
            response.headers["x-trace-id"] = synthetic_tid
        if bind_trace_ids:
            bind_trace_ids(None, None)
        return response

def current_trace_id_hex() -> Optional[str]:
    try:
        from opentelemetry import trace as _t  # type: ignore
    except ImportError:
        return None
    span = _t.get_current_span()
    if span:
        ctx = span.get_span_context()  # type: ignore[attr-defined]
        if getattr(ctx, "trace_id", 0):
            return f"{ctx.trace_id:032x}"
    return None

def inject_trace_context(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Build **sanitised** outbound headers with W3C trace context and a safe `x-trace-id`.
    Hardenings:
    - Do **not** forward any incoming `x-trace-id`, `traceparent`, or `tracestate`.
    - Prefer OTEL propagators; otherwise derive from current logging/OTEL context; otherwise synthesise.
    """
    # 1) Copy & strip any user-supplied context headers (case-insensitive)
    hdrs_in = dict(headers or {})
    hdrs: Dict[str, str] = {k: v for k, v in hdrs_in.items() if k.lower() not in ("x-trace-id","traceparent","tracestate")}

    # 2) Inject from current span if OTEL is present
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(hdrs)  # sets traceparent/tracestate from current context
    except ImportError:
        pass

    # 3) Fallback: ensure `traceparent` exists even without OTEL
    if not any(k.lower() == "traceparent" for k in hdrs.keys()):
        tid_hex = None; sid_hex = None
        try:
            from opentelemetry import trace as _t  # type: ignore
            _sp = _t.get_current_span()
            _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
            if _ctx and getattr(_ctx, "trace_id", 0) and getattr(_ctx, "span_id", 0):
                tid_hex = f"{_ctx.trace_id:032x}"; sid_hex = f"{_ctx.span_id:016x}"
        except ImportError:
            pass
        if not (tid_hex and sid_hex):
           try:
                from core_logging import current_trace_ids  # type: ignore
                tid_hex, sid_hex = current_trace_ids()
           except ImportError:
                tid_hex, sid_hex = (None, None)
        if not tid_hex:
            import uuid as _uuid
            tid_hex = _uuid.uuid4().hex + _uuid.uuid4().hex  # 32 hex
        if not sid_hex:
            import uuid as _uuid
            sid_hex = _uuid.uuid4().hex[:16]  # 16 hex
        hdrs["traceparent"] = f"00-{tid_hex}-{sid_hex}-01"

    # 4) Always set/overwrite safe `x-trace-id`
    def _current_trace_id_hex() -> Optional[str]:
        try:
            from opentelemetry import trace as _t  # type: ignore
            _sp = _t.get_current_span()
            _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
            if _ctx and getattr(_ctx, "trace_id", 0):
                return f"{_ctx.trace_id:032x}"
        except ImportError:
            pass
        try:
            from core_logging import current_trace_ids  # type: ignore
            tid, _ = current_trace_ids()
            return tid
        except ImportError:
            return None
    _tid = _current_trace_id_hex()
    if not _tid:
        import uuid as _uuid
        _tid = _uuid.uuid4().hex + _uuid.uuid4().hex
    hdrs["x-trace-id"] = _tid
    return hdrs

# ---------------------------------------------------------------------------
# Unified tracing setup
# ---------------------------------------------------------------------------
_tracing_setup_done: bool = False

def _normalize_http_endpoint(ep: str) -> str:
    # Ensure HTTP exporter endpoints include the '/v1/traces' suffix.
    if ep.endswith("/v1/traces"):
        return ep
    # Strip trailing slashes and append standard path
    return ep.rstrip("/") + "/v1/traces"

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
        except ImportError:
            OTLPSpanExporter = None  # type: ignore

        # Build resource with service name and optional deployment environment
        attrs = {"service.name": service_name}
        env = (
            os.getenv("DEPLOYMENT_ENVIRONMENT")
            or os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT")
            or os.getenv("DEPLOYMENT")
            or None
        )
        if env:
            attrs["deployment.environment"] = env
        res = Resource.create(attrs)
        tp = TracerProvider(resource=res)
        # Attempt to construct and attach the exporter
        if OTLPSpanExporter is not None:
            try:
                exporter = OTLPSpanExporter(endpoint=_normalize_http_endpoint(endpoint))
                tp.add_span_processor(BatchSpanProcessor(exporter))
                otel_present = True
            except (RuntimeError, ValueError, OSError):
                # Failed to instantiate exporter; proceed without exporting spans
                otel_present = False
        # Register the provider globally regardless of exporter availability
        _trace.set_tracer_provider(tp)
        # Prefer W3C trace context propagation globally
        try:
            from opentelemetry.propagate import set_global_textmap  # type: ignore
            from opentelemetry.propagators.tracecontext import (  # type: ignore
                TraceContextTextMapPropagator,
            )
            set_global_textmap(TraceContextTextMapPropagator())
        except ImportError:
            pass
    except ImportError:
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
            request_id="startup",
        )
    except ImportError:
        pass
