import logging, sys, orjson, os, asyncio
from typing import Any, Optional, Dict, Iterable, List, Tuple
from contextlib import contextmanager as _contextmanager
import time
import inspect
import functools
import contextvars
from core_utils.fingerprints import sha256_hex

# ────────────────────────────────────────────────────────────
# Request-level aggregation & summary emission
# ────────────────────────────────────────────────────────────
class _ReqAgg:
    __slots__ = ("events","timers","last","id_norm","errors","once")
    def __init__(self) -> None:
        self.events: dict[str, dict[str,int]] = {}
        self.timers: dict[str, list[float]] = {}
        self.last: dict[str, Any] = {}
        self.id_norm: list[tuple[str,str]] = []
        self.errors: list[dict[str,Any]] = []
        self.once: set[str] = set()

_REQ_AGG: contextvars.ContextVar[Optional[_ReqAgg]] = contextvars.ContextVar("REQ_AGG", default=None)

def _get_req_agg() -> _ReqAgg:
    agg = _REQ_AGG.get()
    if agg is None:
        agg = _ReqAgg()
        _REQ_AGG.set(agg)
    return agg

def _should_summarize() -> bool:
    # Default to compact summary mode; set LOG_EMIT_MODE=verbose to disable
    return (os.getenv("LOG_EMIT_MODE", "summary").lower() in ("summary","summarize","compact"))

def _should_emit_summary_for_service() -> bool:
    """
    Env-gated switch to allow only one service to emit request summaries.
    Set LOG_SUMMARY_EMIT=0 on leaf services to avoid duplicate summaries.
    """
    return (os.getenv("LOG_SUMMARY_EMIT", "1").lower() in ("1","true","yes","on"))

def _is_error_like(event: str, extras: Dict[str, Any]) -> bool:
    ev = (event or "").lower()
    if "error" in extras or extras.get("level") == "ERROR" or int(extras.get("status_code", 200)) >= 500:
        return True
    # Policy denies are legitimate ACL outcomes – never classify as errors.
    if ev == "opa_decide_denied" or extras.get("error_code") == "OPA_DECIDE_DENIED":
        return False
    # Treat policy fingerprint mismatches as warnings (request proceeds)
    if ev == "policy_fp_mismatch":
        return False
    for k in ("error","failed","exception","invalid","mismatch","timeout"):
        if k in ev:
            return True
    return False

def _always_emit(stage: str, event: str) -> bool:
    # Preserve the canonical bookends even in summary mode
    if stage == "request" and event in ("request_start","request_end"):
        return True
    return False

def _iter_latencies_ms(extras: Dict[str, Any]) -> Iterable[float]:
    v = extras.get("latency_ms")
    if isinstance(v, (int, float)):
        yield float(v)

def _agg_note(stage: str, event: str, extras: Dict[str, Any]) -> None:
    agg = _get_req_agg()
    st = agg.events.setdefault(stage, {})
    st[event] = st.get(event, 0) + 1
    for v in _iter_latencies_ms(extras):
        agg.timers.setdefault(stage, []).append(v)
    # Surface commonly used fingerprints once in the summary
    for k in ("snapshot_etag","policy_fp","allowed_ids_fp","graph_fp","request_id","bundle_fp","prompt_fingerprint","plan_fingerprint"):
        v = extras.get(k) or (extras.get("meta") or {}).get(k)
        if isinstance(v, str) and v:
            agg.last[k] = v
    # Capture last seen HTTP method/target for request_summary clarity
    http = extras.get("http")
    if isinstance(http, dict):
        m = http.get("method")
        t = http.get("target")
        if isinstance(m, str) and m:
            agg.last["method"] = m
        if isinstance(t, str) and t:
            agg.last["path"] = t
    # Collect a few normalization samples when present
    if (event.endswith("id_normalized") or event == "id_normalized"):
        b = extras.get("before") or (extras.get("meta") or {}).get("before")
        a = extras.get("after") or (extras.get("meta") or {}).get("after")
        if isinstance(b,str) and isinstance(a,str) and len(agg.id_norm) < 16:
            agg.id_norm.append((b,a))
    # Keep error crumbs verbatim in the summary payload
    if _is_error_like(event, extras):
        agg.errors.append({
            "stage": stage,
            "event": event,
            "attrs": {k: v for k, v in extras.items() if k not in ("message","event")}
        })

    # Capture first-seen trace/span for later use in summaries (span context may be closed by then)
    tid, sid = current_trace_ids()
    if tid and "trace_id" not in agg.last:   agg.last["trace_id"] = tid

def emit_request_summary(logger: logging.Logger, *, service: Optional[str]=None) -> None:
    """Emit one compact per-request summary line when summary mode is active."""
    if not _should_summarize() or not _should_emit_summary_for_service():
        return
    agg = _REQ_AGG.get()
    if not agg:
        return
    timers = {}
    for stage, vals in agg.timers.items():
        if not vals:
            continue
        srt = sorted(vals)
        n   = len(srt)
        p50 = srt[int(0.5*(n-1))]
        p95 = srt[int(0.95*(n-1))]
        timers[stage] = {
            "count": n,
            "sum_ms": round(sum(srt), 3),
            "p50_ms": round(float(p50), 3),
            "p95_ms": round(float(p95), 3),
            "max_ms": round(max(srt), 3),
        }
    payload = {
        "stage": "summary",
        "service": service or os.getenv("SERVICE_NAME") or logger.name,
        "counts": {k: sum(v.values()) for k,v in agg.events.items()},
        "events": agg.events,
        "timers": timers,
        "id_normalized": {"count": len(agg.id_norm), "samples": agg.id_norm[:4]},
        **agg.last,
        "error_count": len(agg.errors),
    }
    # Summarize cache usage if present
    _cache = (agg.events or {}).get("cache", {})
    _hits  = int(_cache.get("cache.hit", 0))
    _miss  = int(_cache.get("cache.miss", 0))
    _gets  = int(_cache.get("cache.get", 0))
    _total = _gets if _gets else (_hits + _miss)
    if _total:
        payload["cache"] = {
            "gets": _total,
            "hits": _hits,
            "misses": max(0, _total - _hits),
            "hit_rate": round((_hits / _total), 3) if _total else 0.0,
        }
    # Best-effort ensure request_id present
    try:
        rid = current_request_id()
        if rid and not payload.get("request_id"):
            payload["request_id"] = rid
    except Exception:
        pass
    # Attach frozen trace/span if available (top-level fields are allowed)
    tid = agg.last.get("trace_id") or current_trace_ids()[0]
    sid = agg.last.get("span_id")  or current_trace_ids()[1]
    if tid:
        payload["trace_id"] = tid
    # Only include span_id when it's not the synthetic fallback (tid[:16])
    if sid and not (tid and isinstance(sid, str) and sid == tid[:16]):
        payload["span_id"] = sid
    elif sid and tid and sid == tid[:16]:
        # Make it explicit in summaries when we're in fallback correlation mode
        payload["span_synthetic"] = True
    logger.info("request_summary", extra=_sanitize_extra(payload))
    _REQ_AGG.set(None)

# ────────────────────────────────────────────────────────────
# Error helpers (single-line ERRORs + end-of-request rollup)
# ────────────────────────────────────────────────────────────
def record_error(
    code: str,
    *,
    where: str,
    message: str,
    logger: logging.Logger,
    action: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    level: str = "ERROR",
    **extras: Any,
) -> None:
    """
    Emit one normalized ERROR line *and* stash a structured crumb for the
    end-of-request error summary. Safe to call from any failure path.
    """
    agg = _get_req_agg()
    # Keep a normalized crumb for the rollup
    crumb = {
        "code": str(code),
        "where": str(where),
        "message": str(message),
        **({"action": action} if action else {}),
        **({"context": context} if isinstance(context, dict) else {}),
    }
    agg.errors.append(crumb)
    # Immediate single-line ERROR for operators/dashboards
    lvl = (level or "ERROR").upper()
    levelno = getattr(logging, lvl, logging.ERROR)
    payload = {
        "stage": extras.pop("stage", None) or "error",
        "error_code": code,
        "error_message": message,
        "where": where,
        **({"action": action} if action else {}),
        **({"context": context} if isinstance(context, dict) else {}),
        **extras,
    }
    logger.log(levelno, "error", extra=_sanitize_extra(payload))

def _normalize_error_crumbs(crumbs: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Coerce aggregator error crumbs into a uniform shape for rollup. Supports
    both explicit `record_error` entries and heuristic `_agg_note` errors.
    """
    out: List[Dict[str, Any]] = []
    for c in (crumbs or []):
        if "code" in c and "where" in c and "message" in c:
            out.append({k: v for k, v in c.items() if v is not None})
            continue
        # Heuristic crumbs from _agg_note(...) → synthesize a minimal record
        ev  = c.get("event")
        stg = c.get("stage") or "unknown"
        attrs = c.get("attrs") or {}
        code = str(attrs.get("error_code") or attrs.get("code") or (str(ev or "GENERIC").upper().replace(".", "_")))
        msg  = str(attrs.get("error_message") or attrs.get("error") or attrs.get("detail") or ev or "error")
        out.append({
            "code": code,
            "where": stg,
            "message": msg,
            **({"action": attrs.get("action")} if attrs.get("action") else {}),
            **({"context": attrs.get("context")} if isinstance(attrs.get("context"), dict) else {}),
        })
    return (len(out), out)

def emit_request_error_summary(logger: logging.Logger, *, service: Optional[str]=None) -> None:
    """
    Emit a single compact ERROR rollup when the current request accumulated
    any errors. No-op if none were recorded.
    """
    agg = _REQ_AGG.get()
    if not agg or not agg.errors:
        return
    count, errors = _normalize_error_crumbs(agg.errors)
    payload: Dict[str, Any] = {
        "stage": "summary",
        "service": service or os.getenv("SERVICE_NAME") or logger.name,
        "error_count": int(count),
        "errors": errors[:50],  # guard against pathological fan-out
    }
    # Best-effort: include request_id if bound
    try:
        rid = current_request_id()
        if rid and "request_id" not in payload:
            payload["request_id"] = rid
    except Exception:
        pass
    # Optionally derive a coarse "cause" for dashboards (prefix before dot)
    # Prefer precondition-style errors if present; otherwise fall back to first
    if errors:
        codes = [str(e.get("code") or "").lower() for e in errors]
        preferred = next((c for c in codes if c.startswith("precondition")), codes[0])
        payload["cause"] = (preferred.split(".", 1)[0] or "unknown")
    # Gate emission to avoid duplicates across services
    if not _should_emit_summary_for_service():
        return
    # Attach frozen trace/span like the success summary
    tid = (agg.last or {}).get("trace_id") or current_trace_ids()[0]
    sid = (agg.last or {}).get("span_id")  or current_trace_ids()[1]
    if tid: payload["trace_id"] = tid
    if sid: payload["span_id"]  = sid
    logger.error("request_error_summary", extra=_sanitize_extra(payload))

# ────────────────────────────────────────────────────────────
# Global Snapshot-ETag support
# ────────────────────────────────────────────────────────────
# Unit-tests (and, eventually, the ingest/gateway services)
# expect every log record to carry a `snapshot_etag` attribute.
# We expose a simple setter plus a logging.Filter that injects
# the value into each LogRecord as it is emitted.

_SNAPSHOT_ETAG: Optional[str] = None

# Current trace ids (fallback if OTEL not active). We still prefer reading
# from OpenTelemetry when available; see JsonFormatter.format() below.
_TRACE_IDS: contextvars.ContextVar[tuple[Optional[str], Optional[str]]] = \
    contextvars.ContextVar("_TRACE_IDS", default=(None, None))
_REQUEST_ID: contextvars.ContextVar[Optional[str]] = \
    contextvars.ContextVar("_REQUEST_ID", default=None)

def bind_trace_ids(trace_id: Optional[str], span_id: Optional[str]) -> None:
    """Bind trace/span IDs into the local context for logging fallbacks.
    If `span_id` looks synthetic (tid[:16]) and there's no active OTEL span,
    drop it to avoid misleading summaries.
    """
    # Heuristic: treat tid[:16] as synthetic unless a real span is active
    if trace_id and span_id and isinstance(trace_id, str) and isinstance(span_id, str) and span_id == trace_id[:16]:
        try:
            from opentelemetry import trace as _t  # type: ignore
            _ctx = _t.get_current_span().get_span_context()  # type: ignore[attr-defined]
            if not getattr(_ctx, "span_id", 0):
                span_id = None
        except Exception:
            # OTEL not present or no active span → drop synthetic sid
            span_id = None
    try:
        _TRACE_IDS.set((trace_id, span_id))
    except Exception:
        pass

def bind_request_id(request_id: Optional[str]) -> None:
    """Bind the current request_id into the local context for log injection."""
    try:
        _REQUEST_ID.set(request_id)
    except Exception:
        pass

def current_request_id() -> Optional[str]:
    """Return the currently bound request_id (if any)."""
    try:
        return _REQUEST_ID.get()
    except Exception:
        return None

def current_trace_ids() -> tuple[Optional[str], Optional[str]]:
    """Return the currently bound (trace_id, span_id) pair, if any."""
    try:
        return _TRACE_IDS.get()
    except Exception:
        return (None, None)

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

class _RequestIdFilter(logging.Filter):
    """Inject the bound request_id (if any) into LogRecords that lack it."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if getattr(record, "request_id", None) is None:
                rid = _REQUEST_ID.get()
                if rid:
                    record.request_id = rid
        except Exception:
            pass
        return True

# Reserved LogRecord attributes we must not overwrite
_RESERVED: set[str] = {
    "name","msg","args","levelname","levelno",
    "pathname","filename","module","exc_info","exc_text","stack_info",
    "lineno","funcName","created","msecs","relativeCreated",
    "thread","threadName","processName","process","message","asctime",
}

# Top‑level fields allowed by the B5 log‑envelope (§B5 tech‑spec)
_TOP_LEVEL: set[str] = {
    "ts",                 # ISO-8601 UTC
    "level",              # INFO|DEBUG|…
    "service",            # gateway|api_edge|…
    "stage",              # resolve|plan|…
    "latency_ms",         # optional, top‑level
    "request_id", "snapshot_etag",
    "prompt_fingerprint", "plan_fingerprint", "bundle_fingerprint",
    # v3 baseline (prefer *_fp naming); keep *_fingerprint for compat
    "bundle_fp",
    "selector_model_id",
    "message",            # preserved human message
    "policy_fp",
    "allowed_ids_fp",
    "graph_fp",
    "cache_key",
    "status_code",
    "path",
    "method",
    "trace_id",
    "span_id",
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
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(getattr(record, "created", time.time()))),
            "level": record.levelname,
            "service": os.getenv("SERVICE_NAME", record.name),
            # Canonical event key (do not duplicate as `message`)
            "event": record.getMessage(),
        }

        # Attach OTEL trace identifiers if present; otherwise use local fallback.
        trace_id = None
        span_id  = None
        try:
            from opentelemetry import trace as _otel_trace  # type: ignore
            span = _otel_trace.get_current_span()
            if span is not None:
                ctx = span.get_span_context()  # type: ignore[attr-defined]
                # ctx.trace_id is an int; 0 means "invalid"
                if getattr(ctx, "trace_id", 0):
                    trace_id = f"{ctx.trace_id:032x}"
                    span_id  = f"{ctx.span_id:016x}"
        except Exception:
            pass
        if not trace_id:
            # fallback from contextvar installed by trace_span()
            tid, sid = _TRACE_IDS.get()
            trace_id = tid
            span_id  = sid
        if trace_id:
            base["trace_id"] = trace_id

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

        # If a human-friendly message was explicitly provided by callers,
        # preserve it under the canonical `message` key (rare).
        msg_extra = record.__dict__.get("message_extra", None)
        if msg_extra is not None:
            base["message"] = msg_extra

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
        # Sanitize to avoid LogRecord collisions (e.g., "message")
        extra = _sanitize_extra(extra)
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
    is_service_root = "." not in name  # only top-level names own handlers

    if is_service_root:
        # Attach a single JSON StreamHandler **once** for the service root.
        if not logger.handlers:
            handler = DynamicStdoutHandler()
            handler.setFormatter(JsonFormatter())
            logger.addHandler(handler)
        # Service roots terminate propagation to avoid double-emit at the root.
        logger.propagate = False
    else:
        # Leaf/module loggers never own handlers; let them bubble to the service root.
        if logger.handlers:  # scrub any accidental handlers to prevent duplication
            for h in list(logger.handlers):
                logger.removeHandler(h)
        logger.propagate = True

    logger.setLevel(level or os.getenv("SERVICE_LOG_LEVEL", "INFO"))
    # Ensure contextual filters are attached exactly once.
    if not any(isinstance(f, _SnapshotFilter) for f in getattr(logger, "filters", [])):
        logger.addFilter(_SnapshotFilter())
    if not any(isinstance(f, _RequestIdFilter) for f in getattr(logger, "filters", [])):
        logger.addFilter(_RequestIdFilter())
    if is_service_root:
        log_once_process(logger, key="summary_emit_config",
                         event="summary.emit_config", enabled=_should_emit_summary_for_service(),
                         mode=os.getenv("LOG_EMIT_MODE", "summary"))
    return logger

def log_event(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    logger.info(event, extra=_sanitize_extra(kwargs))

# ---------------------------------------------------------------------------#
# log_once – emit a structured line only once per request (by key)           #
# ---------------------------------------------------------------------------#
def log_once(logger: logging.Logger, *, key: str, event: str, **extras: Any) -> None:
    """
    Emit the (event, extras) only once for the current request, keyed by *key*.
    Still contributes to the per-request summary when summary mode is active.
    """
    agg = _get_req_agg()
    payload = {"stage": extras.pop("stage", None) or extras.pop("phase", None) or "misc", **extras}
    _agg_note(payload["stage"], event, payload)
    if key in agg.once:
        return
    agg.once.add(key)
    logger.info(event, extra=_sanitize_extra(payload))

# ---------------------------------------------------------------------------#
# Internal helper – emit exactly one structured log line                      #
# ---------------------------------------------------------------------------#
def _emit_stage_log(logger: logging.Logger, stage: str, event: str, **extras: Any):
    payload = {"stage": stage, **extras}
    # In summary mode: aggregate most breadcrumbs, but always keep request bookends and errors.
    if _should_summarize() and not _always_emit(stage, event) and not _is_error_like(event, payload):
        _agg_note(stage, event, payload)
        return
    _agg_note(stage, event, payload)  # keep stats even when emitting
    logger.info(event, extra=_sanitize_extra(payload))

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

# ────────── Unified decorator **and** ctx-manager helper (bridged to OTEL) ───
class _TraceSpan:
    def __init__(self, name: str, logger: logging.Logger, **fixed):
        self._name, self._fixed, self._logger = name, fixed, logger
        self._otel_cm = None
        self._span = None

    # --- context-manager ---
    def __enter__(self):
        self._t0 = time.time()
        # Start real OTEL span if available, while preserving existing logs.
        try:
            from opentelemetry import trace as _otel_trace  # type: ignore
            tracer = _otel_trace.get_tracer(os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "batvault")
            self._otel_cm = tracer.start_as_current_span(self._name)
            self._span = self._otel_cm.__enter__()  # type: ignore[assignment]
            try:
                ctx = self._span.get_span_context()  # type: ignore[attr-defined]
                # Bind only **valid** (non-zero) OTEL IDs; otherwise leave unbound
                if getattr(ctx, "trace_id", 0):
                    # Capture the current context before overwriting it so it can be restored on exit.
                    try:
                        # _TRACE_IDS.set returns a Token that can be used to restore the previous value.
                        self._trace_token = _TRACE_IDS.set((f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"))
                    except Exception:
                        # If contextvars are not supported use a no-op sentinel.
                        self._trace_token = None
            except Exception:
                pass
        except Exception:
            pass
        # Use `stage` from fixed metadata if provided; otherwise default to span name.
        stage_value = self._fixed.get("stage", self._name)
        extras = {k: v for k, v in self._fixed.items() if k != "stage"}
        log_stage(self._logger, stage_value, f"{self._name}.start", **extras)
        return self

    def __exit__(self, exc_type, exc, tb):
        log_stage(
            self._logger,
            self._fixed.get("stage", self._name),
            f"{self._name}.end",
            latency_ms=int((time.time() - self._t0) * 1_000),
            **{k: v for k, v in self._fixed.items() if k != "stage"},
        )
        try:
            if self._otel_cm:
                self._otel_cm.__exit__(exc_type, exc, tb)  # type: ignore[union-attr]
        finally:
            # Restore any previously bound trace ids on exit.  When a token
            # exists (set during __enter__), reset will revert to the prior
            # context rather than clearing unconditionally.  If no token
            # exists (e.g. OTEL span not recording), this is a no-op.
            try:
                if self._trace_token is not None:
                    _TRACE_IDS.reset(self._trace_token)  # type: ignore[arg-type]
                else:
                    # When no token was set, fall back to clearing the ids.
                    _TRACE_IDS.set((None, None))
            except Exception:
                pass

    # make span attributes available to call-sites even when they use
    # `with trace_span(...) as sp:`.  When OTEL is not initialised this is a no-op.
    def set_attribute(self, key: str, value: Any) -> None:
        try:
            if self._span is not None:
                self._span.set_attribute(key, value)  # type: ignore[attr-defined]
        except Exception:
            pass

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

def _sanitize_extra(extra: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Remove/rename keys in `extra` that would collide with LogRecord attributes.
    - `message` is remapped to `message_extra` to preserve content.
    - all other collisions are namespaced as `meta_<key>`.
    """
    if not extra:
        return {}
    safe: Dict[str, Any] = {}
    for k, v in extra.items():
        lk = str(k)
        # Flatten user-provided nested `meta` to avoid meta.meta
        if lk == "meta" and isinstance(v, dict):
            for mk, mv in v.items():
                # don't collide with LogRecord attrs
                mk_norm = str(mk)
                if mk_norm in _RESERVED:
                    safe[f"meta_{mk_norm}"] = mv
                else:
                    safe[mk_norm] = mv
            continue

        if lk in _RESERVED:
            if lk == "message":
                safe["message_extra"] = v
            else:
                safe[f"meta_{lk}"] = v
        else:
            safe[lk] = v
    return safe

# ---------------------------------------------------------------------------#
# log_once_process – emit a line only once per process key (strategic logs)     #
# ---------------------------------------------------------------------------#
_ONCE_KEYS: set[str] = set()
def log_once_process(logger: logging.Logger, key: str, *, level: int = logging.INFO, event: str, **kwargs: Any) -> None:
    """
    Emit a structured log exactly once per *key* for the lifetime of the process.
    Useful for one-shot diagnostics (e.g., schema directory resolution).
    """
    if key in _ONCE_KEYS:
        return
    _ONCE_KEYS.add(key)
    logger.log(level, event, extra=_sanitize_extra(kwargs))

# ────────────────────────────────────────────────────────────
# Cache logging helpers (shared across services)
# ────────────────────────────────────────────────────────────
def cache_key_fp(key: Any) -> str:
    """Deterministic fingerprint for cache keys (avoid logging raw keys)."""
    s = str(key)
    # Use shared helper to keep hashing consistent across services
    return "sha256:" + sha256_hex(s)[:16]

def log_cache_hit(logger: logging.Logger, *, backend: str, namespace: str, key: Any,
                  ttl_remaining_ms: Optional[int] = None, shared: bool = True, latency_ms: Optional[int] = None) -> None:
    log_stage(logger, "cache", "cache.hit",
              backend=str(backend), namespace=str(namespace), key_fp=cache_key_fp(key),
              shared=bool(shared), ttl_remaining_ms=ttl_remaining_ms, latency_ms=latency_ms)

def log_cache_miss(logger: logging.Logger, *, backend: str, namespace: str, key: Any,
                   shared: bool = True, latency_ms: Optional[int] = None) -> None:
    log_stage(logger, "cache", "cache.miss",
              backend=str(backend), namespace=str(namespace), key_fp=cache_key_fp(key),
              shared=bool(shared), latency_ms=latency_ms)

def log_cache_set(logger: logging.Logger, *, backend: str, namespace: str, key: Any,
                  ttl_ms: Optional[int] = None, bytes: Optional[int] = None, shared: bool = True, latency_ms: Optional[int] = None) -> None:
    log_stage(logger, "cache", "cache.set",
              backend=str(backend), namespace=str(namespace), key_fp=cache_key_fp(key),
              shared=bool(shared), ttl_ms=ttl_ms, bytes=bytes, latency_ms=latency_ms)
