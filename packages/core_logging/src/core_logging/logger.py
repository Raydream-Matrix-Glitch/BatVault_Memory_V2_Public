import logging, sys, time, orjson, os
from typing import Any


# Reserved LogRecord attributes we must not overwrite
_RESERVED: set[str] = {
    "name","msg","args","levelname","levelno",
    "pathname","filename","module","exc_info","exc_text","stack_info",
    "lineno","funcName","created","msecs","relativeCreated",
    "thread","threadName","processName","process",
}

# Top‑level fields allowed by the B5 log‑envelope (§B5 tech‑spec)
_TOP_LEVEL: set[str] = {
    "timestamp",          # ISO‑8601 UTC
    "level",              # INFO|DEBUG|…
    "service",            # gateway|api_edge|…
    "stage",              # resolve|plan|…
    "latency_ms",         # optional, top‑level
    "request_id", "snapshot_etag",
    "prompt_fingerprint", "plan_fingerprint", "bundle_fingerprint",
    "selector_model_id",
    "message",            # preserved human message
}

def _default(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    raise TypeError

class JsonFormatter(logging.Formatter):
    """Emit structured JSON logs that comply with the B5 envelope.

    Top‑level keys follow the spec; everything else is nested under ``meta``.
    """

    def format(self, record: logging.LogRecord) -> str:
        # --- fixed top‑level fields -----------------------------------------
        base: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(getattr(record, "created", time.time()))),
            "level": record.levelname,
            "service": os.getenv("SERVICE_NAME", record.name),
            "message": record.getMessage(),
        }

        meta: Dict[str, Any] = {}

        # ── merge structured extras ─────────────────────────────────────────
        for key, val in record.__dict__.items():
            if key in _RESERVED:
                continue  # skip LogRecord internals

            # keep allowed top‑level attrs flat; everything else → meta
            if key in _TOP_LEVEL:
                base[key] = val
            else:
                meta[key] = val

        if meta:
            base["meta"] = meta

        return orjson.dumps(base, default=_default).decode("utf-8")

def get_logger(name: str="app", level: str|None=None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(level or os.getenv("SERVICE_LOG_LEVEL","INFO"))
    return logger

def log_event(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    logger.info(event, extra=kwargs)

# ---------------------------------------------------------------------------#
# Internal helper – emit exactly one structured log line                      #
# ---------------------------------------------------------------------------#
def _emit_stage_log(logger: logging.Logger, stage: str, event: str, **extras: Any):
    payload = {"stage": stage, **extras}
    logger.info(event, extra={k: v for k, v in payload.items() if k not in _RESERVED})

# ---------------------------------------------------------------------------#
# log_stage – imperative **and** decorator utility (§B5 tech-spec)           #
# ---------------------------------------------------------------------------#
def log_stage(logger: logging.Logger, stage: str, event: str, **fixed: Any):
    """
    *Imperative*  →  log_stage(logger, "gateway", "v2_query_end", request_id=req.id)
    *Decorator*   →  @log_stage(logger, "gateway", "v2_query")
                     async def v2_query(...):
                         ...
    Also exposes ``.ctx`` so it can be used as a context-manager just like
    ``trace_span``.
    """
    # fire-and-forget so existing call-sites stay untouched
    _emit_stage_log(logger, stage, event, **fixed)

    import asyncio, time
    from contextlib import contextmanager

    # ---------------- decorator ------------------------------------------ #
    def _decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            async def _aw(*a, **kw):
                t0 = time.perf_counter()
                try:
                    return await fn(*a, **kw)
                finally:
                    _emit_stage_log(
                        logger, stage, f"{event}.done",
                        latency_ms=(time.perf_counter() - t0) * 1000,
                        **fixed,
                    )
            return _aw

        def _w(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                _emit_stage_log(
                    logger, stage, f"{event}.done",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    **fixed,
                )
        return _w

    # ---------------- ctx-manager ---------------------------------------- #
    @contextmanager
    def _ctx(**dynamic):
        _emit_stage_log(logger, stage, f"{event}.start", **(fixed | dynamic))
        t0 = time.perf_counter()
        try:
            yield
        finally:
            _emit_stage_log(
                logger, stage, f"{event}.done",
                latency_ms=(time.perf_counter() - t0) * 1000,
                **(fixed | dynamic),
            )

    _decorator.ctx = _ctx
    return _decorator

# -- upgraded helper: decorator OR ctx-manager, sync OR async --------------
import asyncio, types
from contextlib import contextmanager, asynccontextmanager, nullcontext

try: from opentelemetry import trace as _otel_trace          # optional
except Exception: _otel_trace = None                         # pragma: no cover

def _tracer():                                               # local helper
    return _otel_trace.get_tracer("batvault") if _otel_trace else None

def trace_span(name: str, **fixed):
    """
    Usage 1 – decorator:  
        @trace_span("resolve") async def fn(...):
    Usage 2 – ctx-manager:  
        with trace_span.ctx("plan"): ...
    Falls back to a no-op when OTEL is absent (unit tests, local dev).
    """
    tracer = _tracer()

    # ➊ decorator ----------------------------------------------------------
    def _decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            async def _aw(*a, **kw):
                ctx = tracer.start_as_current_span(name) if tracer else nullcontext()
                async with (asynccontextmanager(lambda: ctx)() if hasattr(ctx, "__aenter__") else ctx):
                    span = _otel_trace.get_current_span() if tracer else None
                    if span: [span.set_attribute(k, v) for k, v in fixed.items()]
                    return await fn(*a, **kw)
            return _aw
        def _w(*a, **kw):
            with (tracer.start_as_current_span(name) if tracer else nullcontext()) as span:
                if span: [span.set_attribute(k, v) for k, v in fixed.items()]
                return fn(*a, **kw)
        return _w

    # ➋ ctx-manager --------------------------------------------------------
    @contextmanager
    def _ctx(**dynamic):
        with (tracer.start_as_current_span(name) if tracer else nullcontext()) as span:
            if span:
                for k, v in (fixed | dynamic).items(): span.set_attribute(k, v)
            yield span

    _decorator.ctx = _ctx
    return _decorator
