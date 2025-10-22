from __future__ import annotations
from typing import Any, Optional

class RedisCache:
    """
    Thin async wrapper over a provided redis.asyncio client.
    No retries here; callers own policies and timeouts.
    """
    def __init__(self, client: Any):
        if client is None:
            raise ValueError("RedisCache requires a valid redis client")
        self._r = client

    async def get(self, key: str) -> Optional[bytes]:
        return await self._r.get(key)

    async def setex(self, key: str, ttl_seconds: int, value: bytes) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8")
        elif not isinstance(value, (bytes, bytearray)):
            raise TypeError("RedisCache.setex expects bytes or str")
        await self._r.setex(key, ttl_seconds, value)