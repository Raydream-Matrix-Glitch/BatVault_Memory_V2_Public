from __future__ import annotations
from typing import Optional
from redis import asyncio as aioredis
from core_config import get_settings
from core_logging import get_logger

_logger = get_logger("gateway.redis")
_pool: Optional[aioredis.Redis] = None

def get_redis_pool() -> aioredis.Redis:
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = aioredis.from_url(
            s.redis_url,  # type: ignore[attr-defined]
            encoding="utf-8",
            decode_responses=True,
            max_connections=getattr(s, "redis_max_connections", 100),
        )
        try:
            _logger.info({"stage": "redis", "event": "pool_init", "url": s.redis_url})
        except Exception:
            pass
    return _pool  # type: ignore[return-value]