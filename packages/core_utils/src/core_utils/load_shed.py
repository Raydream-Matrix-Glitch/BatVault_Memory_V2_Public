from __future__ import annotations
import asyncio
import os
from contextvars import ContextVar
from typing import Optional
from core_utils.backoff import sleep_with_jitter
from core_logging import get_logger, log_stage
try:
    from redis.exceptions import RedisError  # type: ignore
except Exception:  # pragma: no cover
    class RedisError(Exception): pass  # type: ignore
from core_config import get_settings
from core_cache.redis_client import get_redis_pool

_logger = get_logger("core_utils.load_shed")

# Cached flag (async-safe)
_load_shed_flag: ContextVar[bool] = ContextVar("_load_shed_flag", default=False)
_refresh_task: asyncio.Task | None = None
_last_log_state: Optional[bool] = None
_last_log_cycle: int = 0

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
    except (ImportError, AttributeError, RuntimeError):
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

            # Export a simple gauge for dashboards (1.0 when shedding).
            try:
                from core_metrics import gauge as _gauge
                _gauge("gateway_load_shed_enabled", 1.0 if flag else 0.0)
            except (ImportError, AttributeError):
                pas

            # Throttle logs: only on state change, or every N cycles (default 60).
            global _last_log_state, _last_log_cycle
            try:
                heartbeat_cycles = int(os.getenv("LOAD_SHED_HEARTBEAT_CYCLES", "60"))
            except (ValueError, TypeError):
                heartbeat_cycles = 60

            should_log = (flag != _last_log_state) or ((cycle - _last_log_cycle) >= heartbeat_cycles)
            if should_log:
                log_stage(
                    _logger, "load_shed", "refresh_cycle",
                    task_id=task_id, cycle=cycle, enabled=flag,
                    request_id=task_id,
                )
                _last_log_state = flag
                _last_log_cycle = cycle
        except (RedisError, ConnectionError, OSError, RuntimeError, ValueError, TypeError) as e:
            log_stage(
                _logger, "load_shed", "refresh_error",
                task_id=task_id, cycle=cycle, error=str(e), request_id=task_id,
            )
        await sleep_with_jitter(1, base=float(period_s), jitter=0.0)

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
    # ContextVar.get() does not raise here; keep defensive, narrow
    try:
        return bool(_load_shed_flag.get())
    except (RuntimeError, LookupError):
        return False