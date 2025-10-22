from __future__ import annotations
from typing import Optional, Any
from core_config import get_settings
from core_logging import get_logger, log_stage

# Try to import the real asyncio Redis client. Fall back to fakeredis for dev/tests.
try:
    from redis import asyncio as aioredis  # type: ignore
except ImportError:  # pragma: no cover - optional dev/test fallback
    try:
        import fakeredis  # type: ignore
        class _FakeAsyncRedis:
            def __init__(self):
                self._r = fakeredis.FakeRedis()
            async def get(self, *a, **kw):
                return self._r.get(*a, **kw)
            async def set(self, *a, **kw):
                return self._r.set(*a, **kw)
            async def setex(self, *a, **kw):
                return self._r.setex(*a, **kw)
        class _Shim:
            @staticmethod
            def from_url(*_a, **_kw):
                return _FakeAsyncRedis()
        aioredis = _Shim()  # type: ignore
    except ImportError:
        aioredis = None  # type: ignore

_logger = get_logger("core_cache.redis")
_pool: Optional[Any] = None

def get_redis_pool() -> Any:
    """Return a shared asyncio Redis client/pool.

    Consumers should treat the returned object as an async client. In dev/tests
    where Redis isn't available, a no-op fake may be returned.
    """
    global _pool
    if _pool is None:
        s = get_settings()
        if aioredis is None:
            raise RuntimeError("redis asyncio client not available")
        _pool = aioredis.from_url(  # type: ignore[attr-defined]
            getattr(s, "redis_url"),
            encoding="utf-8",
            decode_responses=True,
            max_connections=getattr(s, "redis_max_connections", 100),
        )
        log_stage(_logger, "redis", "pool_init",
                  url=getattr(s, "redis_url", ""), request_id="startup")
    return _pool