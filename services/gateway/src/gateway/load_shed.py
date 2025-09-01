from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Optional

from core_logging import get_logger
from .logging_helpers import stage as log_stage
from core_config import get_settings
from .redis import get_redis_pool

_logger = get_logger("gateway.load_shed")

# Cached flag (async-safe)
_load_shed_flag: ContextVar[bool] = ContextVar("_load_shed_flag", default=False)
_refresh_task: asyncio.Task | None = None

async def _refresh_loop(period_s: float) -> None:
    """
    Background refresher that polls Redis for the load-shed flag and caches it
    locally. Emits structured logs each cycle with a deterministic task_id and
    monotonic cycle counter.
    """
    from core_utils.ids import stable_short_id
    settings = get_settings()
    task_id = f"load_shedder:{stable_short_id(getattr(settings, 'redis_url', ''))}"
    cycle = 0
    pool = None
    try:
        pool = get_redis_pool()
    except Exception:
        pool = None
    while True:
        cycle += 1
        try:
            val = None
            if pool is not None:
                res = pool.get("gateway:load_shed")
                val = (await res) if hasattr(res, "__await__") else res
            flag = bool(str(val or "").strip() == "1")
            _load_shed_flag.set(flag)
            log_stage("load_shed", "refresh_cycle",
                task_id=task_id, cycle=cycle, enabled=flag
            )
        except Exception as e:
            log_stage("load_shed", "refresh_error",
                task_id=task_id, cycle=cycle, error=str(e)
            )
        await asyncio.sleep(max(0.1, float(period_s)))

def start_background_refresh(period_ms: int = 300) -> None:
    """Start the refresher loop if not already running."""
    global _refresh_task
    if _refresh_task is None or _refresh_task.done():
        loop = asyncio.get_event_loop()
        _refresh_task = loop.create_task(_refresh_loop(period_ms / 1000.0))

def stop_background_refresh() -> None:
    """Cancel the background refresher if running."""
    global _refresh_task
    if _refresh_task is not None:
        _refresh_task.cancel()
        _refresh_task = None

def should_load_shed() -> bool:
    """Return the last cached flag without performing I/O."""
    try:
        return bool(_load_shed_flag.get())
    except Exception:
        return False