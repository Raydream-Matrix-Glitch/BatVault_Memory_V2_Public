import time
import redis
import httpx
from core_config import get_settings

settings = get_settings()

def should_load_shed() -> bool:
    """
    Return True when Redis is too slow or Memory‐API is returning 5xx.
    """
    # --- Redis latency check -------------------------------------------
    try:
        r = redis.Redis.from_url(settings.redis_url)
        t0 = time.perf_counter()
        r.ping()
        redis_latency_ms = (time.perf_counter() - t0) * 1000
    except Exception:
        return True
    if redis_latency_ms > getattr(settings, "load_shed_redis_threshold_ms", 100):
        return True

    # --- Memory‐API 5xx check -----------------------------------------
    try:
        resp = httpx.get(f"{settings.memory_api_url}/healthz", timeout=1.0)
    except Exception:
        return True
    if resp.status_code >= 500:
        return True

    return False