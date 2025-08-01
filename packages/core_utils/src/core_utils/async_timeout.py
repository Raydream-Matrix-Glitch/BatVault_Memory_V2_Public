import asyncio, logging
from typing import Awaitable, TypeVar

from core_config.constants import timeout_for_stage

T = TypeVar("T")


async def run_with_stage_timeout(stage: str, task: Awaitable[T], logger: logging.Logger) -> T:
    """Executes *task* under the per-stage budget; raises on timeout (A-2)."""
    timeout_s = timeout_for_stage(stage)
    try:
        return await asyncio.wait_for(task, timeout_s)
    except asyncio.TimeoutError:
        logger.warning("stage_timeout", extra={"stage": stage, "timeout_s": timeout_s})
        raise