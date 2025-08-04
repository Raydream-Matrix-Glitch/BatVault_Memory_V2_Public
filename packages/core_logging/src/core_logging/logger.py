import logging, sys, orjson, os, asyncio
from typing import Any, Optional, Dict
from contextlib import contextmanager as _contextmanager
import time
import inspect
import functools

# ────────────────────────────────────────────────────────────
# Global Snapshot-ETag support
# ────────────────────────────────────────────────────────────
# Unit-tests (and, eventually, the ingest/gateway services)
# expect every log record to carry a `snapshot_etag` attribute.
# We expose a simple setter plus a logging.Filter that injects
# the value into each LogRecord as it is emitted.

_SNAPSHOT_ETAG: Optional[str] = None




def set_snapshot_etag(value: Optional[str]) -> None:          # pragma: no cover
    """
    Bind *value* as the current ``snapshot_etag``.  
    Passing ``None`` clears the binding.
    """
    global _SNAPSHOT_ETAG
    _SNAPSHOT_ETAG = value


class _SnapshotFilter(logging.Filter):
    """Inject the globally-configured ``snapshot_etag`` (if any)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _SNAPSHOT_ETAG is not None:
            record.snapshot_etag = _SNAPSHOT_ETAG
        return True


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

class StructuredLogger(logging.Logger):
    """
    A drop-in `logging.Logger` replacement that **accepts arbitrary keyword
    arguments** (e.g. `logger.info("msg", stage="plan")`) and transparently
    merges them into the `extra` mapping.  

    This prevents the `TypeError` raised by the standard library in
    Python ≥ 3.11 and lets test-suites (and production code) attach structured
    fields without boiler-plate.
    """

    def _log(                                   # noqa: PLR0913 – keep signature
        self,
        level: int,
        msg: str,
        args,
        exc_info=None,
        extra: Dict[str, Any] | None = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        **kwargs: Any,
    ) -> None:
        if kwargs:                              # merge kw-args → extra-dict
            extra = {**(extra or {}), **kwargs}
        super()._log(
            level,
            msg,
            args,
            exc_info=exc_info,
            extra=extra,
            stack_info=stack_info,
            stacklevel=stacklevel,
        )


class DynamicStdoutHandler(logging.StreamHandler):
    """
    Ensures every *emit* writes to **the current** `sys.stdout`.

    Unit tests (`redirect_stdout(...)`) replace `sys.stdout` *after* the logger
    has been instantiated; refreshing the stream on each call guarantees the
    log line is captured by the redirected buffer.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.setStream(sys.stdout)                      # always up-to-date
        super().emit(record)


# Make the subclass the default for *new* loggers created after this import
logging.setLoggerClass(StructuredLogger)


def get_logger(name: str = "app", level: str | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:                      # one-shot configuration guard
        handler = DynamicStdoutHandler() 
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        # ------------------------------------------------------------------
        # Allow LogRecords to bubble up to the *root* logger so that
        # third-party tooling (pytest-caplog, opentelemetry, Sentry, …) can
        # observe them without having to attach a handler to every leaf logger.
        #
        # In typical production deployments the root logger either has **no**
        # handlers (→ no duplicate output) or is configured exclusively for
        # out-of-process shipping.  If a human-readable StreamHandler *is*
        # attached higher up the tree, a second, plain-text copy of the log
        # line may appear.  That edge-case has never been part of the public
        # contract, but if it matters you can opt-out by setting
        # `logger.propagate = False` at application bootstrap.
        # ------------------------------------------------------------------
        logger.propagate = True
        logger.setLevel(level or os.getenv("SERVICE_LOG_LEVEL", "INFO"))

        # ensure the snapshot-etag filter is attached exactly once
        logger.addFilter(_SnapshotFilter())
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

# ────────── Unified decorator **and** ctx-manager helper ──────────
class _TraceSpan:
    def __init__(self, name: str, logger: logging.Logger, **fixed):
        self._name, self._fixed, self._logger = name, fixed, logger

    # --- context-manager ---
    def __enter__(self):
        self._t0 = time.time()
        log_stage(self._logger, self._name, "start", **self._fixed)
        return self

    def __exit__(self, exc_type, exc, tb):
        log_stage(
            self._logger,
            self._name,
            "end",
            latency_ms=int((time.time() - self._t0) * 1_000),
            **self._fixed,
        )

    def __call__(self, fn):
        """
        Decorator entry-point.
        We proxy via *args/**kwargs but explicitly set `__signature__`
        so FastAPI (and any other DI that relies on `inspect.signature`)
        still sees the *original* parameters. That prevents spurious
        “required query param” artefacts in OpenAPI or runtime validation.
        """
        sig = inspect.signature(fn)

        if asyncio.iscoroutinefunction(fn):
            async def _wrapped(*args, **kwargs):
                with self:
                    return await fn(*args, **kwargs)
        else:
            def _wrapped(*args, **kwargs):
                with self:
                    return fn(*args, **kwargs)

        functools.update_wrapper(_wrapped, fn)      # keeps name, docstring, etc.
        _wrapped.__signature__ = sig               # <- **critical line**
        return _wrapped

    @_contextmanager
    def ctx(self, **dynamic):
        with _TraceSpan(self._name, self._logger, **{**self._fixed, **dynamic}):
            yield


# ---------- public factory ------------------------------------------------
def trace_span(name: str, *, logger: logging.Logger | None = None, **fixed):
    """
    `logger` is optional; when omitted we fall back to the service-level logger
    named *app* so call-sites stay boiler-plate free.
    """
    return _TraceSpan(name, logger or get_logger("app"), **fixed)
