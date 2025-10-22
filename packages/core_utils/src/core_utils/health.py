"""
core_utils.health – health-check routes for FastAPI services.

Provides attach_health_routes() to wire /healthz and /readyz with custom
 liveness and readiness checks. No legacy aliases are exposed.
"""

import asyncio
from typing import Awaitable, Callable, Mapping, Union

from fastapi import APIRouter, FastAPI, Request
from core_config import get_settings

# A health check can return:
#  - bool
#  - dict (arbitrary JSON body)
#  - Awaitable of either
HealthCheck = Callable[[], Union[bool, dict, Awaitable[Union[bool, dict]]]]
HealthChecks = Mapping[str, HealthCheck]


def attach_health_routes(app: FastAPI, *, checks: HealthChecks) -> None:
    """
    Register health-check endpoints on the app.

    Args:
        app: FastAPI application
        checks: mapping with keys "liveness" and/or "readiness" to callables.
            Liveness check should return bool or dict.
            Readiness check should return bool or dict.

    Endpoints:
        GET /healthz -> { "status": "ok" | "fail" } or custom dict.
        GET /readyz -> readiness check result directly if dict, or
                       { "ready": <bool> }.
    """
    router = APIRouter()

    async def _run_check(fn: HealthCheck) -> Union[bool, dict]:
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            return res
        except Exception:
            return False

    # Optional rate limiter (e.g., slowapi); no-op if absent.
    limiter = getattr(app.state, "limiter", None)
    _default_limit = get_settings().api_rate_limit_default

    def _limit(fn):
        return limiter.limit(_default_limit)(fn) if limiter else fn

    # ── Liveness ────────────────────────────────────────────────────────────
    if "liveness" in checks:
        @_limit
        @router.get("/healthz")
        async def _healthz(request: Request):
            res = await _run_check(checks["liveness"])
            if isinstance(res, dict):
                return res
            return {"status": "ok" if bool(res) else "fail"}
    else:
        @router.get("/healthz")
        async def _healthz_default(request: Request):
            return {"status": "ok"}

    # ── Readiness ───────────────────────────────────────────────────────────
    if "readiness" in checks:
        @_limit
        @router.get("/readyz")
        async def _readyz(request: Request):
            res = await _run_check(checks["readiness"])
            if isinstance(res, dict):
                return res
            return {"ready": bool(res)}
    else:
        @router.get("/readyz")
        async def _readyz_default(request: Request):
            return {"ready": True}

    app.include_router(router)
