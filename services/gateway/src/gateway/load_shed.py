import time
import redis
import httpx
from core_logging import trace_span, log_stage
from core_config import get_settings

settings = get_settings()

def should_load_shed() -> bool:
    """Heuristic load-shedding guard (spec §N)."""
    with trace_span("gateway.load_shed"):
        # --- Redis latency check ----------------------------------------
        try:
            r = redis.Redis.from_url(settings.redis_url, socket_timeout=0.10)
            t0 = time.perf_counter()
            r.ping()
            redis_latency_ms = (time.perf_counter() - t0) * 1000
        except Exception:
            log_stage(None, "gateway", "load_shed_redis_down")
            return True
        if redis_latency_ms > getattr(settings, "load_shed_redis_threshold_ms", 100):
            return True

        # --- Memory-API 5xx check --------------------------------------
        try:
            resp = httpx.get(f"{settings.memory_api_url}/healthz", timeout=1.0)
        except Exception:
            log_stage(None, "gateway", "load_shed_backend_unreachable")
            return True
        if resp.status_code >= 500:
            log_stage(None, "gateway", "load_shed_backend_5xx", status=resp.status_code)
            return True

        return False