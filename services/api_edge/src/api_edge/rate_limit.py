"""
Tiny token-bucket rate-limiter used only by the API-edge health/tests.
Replaces SlowAPI to remove an unnecessary dependency.
"""

import time
from collections import defaultdict
from typing import Dict

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class RateLimitExceeded(Exception):
    """Raised when a client exceeds the configured request rate."""


# --------------------------------------------------------------------------- #
# Bucket + middleware
# --------------------------------------------------------------------------- #


class _TokenBucket:
    __slots__ = ("capacity", "refill_per_sec", "tokens", "last")

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = capacity
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token-bucket middleware (very small, zero-dep).

    * `exclude_paths` lets ops & tests mark endpoints that must **never** be throttled
      (e.g. `/healthz`, `/readyz`, `/metrics`), so future additions need *no* code changes.
    """

    def __init__(
        self,
        app,
        *,
        capacity: int,
        refill_per_sec: float,
        exclude_paths: tuple[str, ...] = ("/healthz", "/readyz", "/metrics"),
    ):  # noqa: D401
        super().__init__(app)
        self._capacity = capacity
        self._refill_per_sec = refill_per_sec
        self._exclude_paths = set(exclude_paths)
        self._buckets: Dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(capacity, refill_per_sec),
        )

    async def dispatch(self, request: Request, call_next):  # noqa: D401
        if request.url.path in self._exclude_paths:
            return await call_next(request)
        key = (request.client.host if request.client else "global") or "global"
        if self._buckets[key].allow():
            return await call_next(request)
        raise HTTPException(status_code=429, detail="Too Many Requests")