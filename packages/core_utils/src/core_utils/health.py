"""
core_utils.health – health-check routes for FastAPI services.

Provides attach_health_routes() to wire /healthz and /readyz with
custom liveness and readiness checks.
"""

import asyncio
from typing import Awaitable, Callable, Mapping, Union

from fastapi import APIRouter, FastAPI

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
        GET /healthz -> { "ok": <bool> } or custom dict.
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

    # Liveness endpoint
    if "liveness" in checks:
        @router.get("/healthz")
        async def _healthz():
            res = await _run_check(checks["liveness"])
            if isinstance(res, dict):
                return res
            return {"ok": bool(res)}
    else:
        @router.get("/healthz")
        async def _healthz_default():
            return {"ok": True}

    # Readiness endpoint
    if "readiness" in checks:
        @router.get("/readyz")
        async def _readyz():
            res = await _run_check(checks["readiness"])
            if isinstance(res, dict):
                return res
            return {"ready": bool(res)}
    else:
        @router.get("/readyz")
        async def _readyz_default():
            return {"ready": True}

    app.include_router(router)
