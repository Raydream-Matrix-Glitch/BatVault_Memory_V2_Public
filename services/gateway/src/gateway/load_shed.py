import time
import contextvars
import redis
import httpx
from core_observability.otel import inject_trace_context
from core_logging import get_logger, trace_span, log_stage
from core_config import get_settings

logger = get_logger("gateway")

settings = get_settings()

# --------------------------------------------------------------------------- #
#  Context flag – lets any downstream stage ask “are we in load-shed mode?”  #
# --------------------------------------------------------------------------- #
_load_shed_flag: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "gateway_load_shed_flag", default=False
)

def is_load_shed_active() -> bool:
    """Return ``True`` when the *current request* is operating in load-shed mode."""
    return _load_shed_flag.get()

def should_load_shed() -> bool:
    """
    The guard can be **globally disabled** by setting
    `LOAD_SHED_ENABLED=false` (or `settings.load_shed_enabled = False`).
    This prevents unit/CI runs – which often lack Redis or Memory-API –
    from being spuriously throttled while leaving the mechanism active in
    staging and production.
    """

    # ── Fast-exit when the feature-flag is off ──────────────────────────
    if not getattr(settings, "load_shed_enabled", False):
        return False

    triggered = False

    with trace_span("gateway.load_shed"):
        # ---- Redis health --------------------------------------------------
        try:
            r = redis.Redis.from_url(settings.redis_url, socket_timeout=0.10)
            t0 = time.perf_counter()
            r.ping()
            redis_latency_ms = (time.perf_counter() - t0) * 1000
            if redis_latency_ms > getattr(settings, "load_shed_redis_threshold_ms", 100):
                triggered = True
        except Exception:  # redis down / unreachable
            log_stage(logger, "gateway", "load_shed_redis_down")
            triggered = True

        # ---- Memory-API health --------------------------------------------
        if not triggered:  # short-circuit if already shedding
            try:
                resp = httpx.get(
                    f"{settings.memory_api_url}/healthz",
                    timeout=1.0,
                    headers=inject_trace_context({}),
                )
                if resp.status_code >= 500:
                    log_stage(
                        logger, "gateway", "load_shed_backend_5xx", status=resp.status_code
                    )
                    triggered = True
            except Exception:  # backend unreachable
                log_stage(logger, "gateway", "load_shed_backend_unreachable")
                triggered = True

    _load_shed_flag.set(triggered)
    return triggered