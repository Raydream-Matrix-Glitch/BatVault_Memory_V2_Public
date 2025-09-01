"""Utility routines for retry and jitter policies.

This module centralizes common backoff patterns (e.g., jittered sleeps)
so that services can depend on a single implementation.  Future
enhancements such as exponential backoff or configurable jitter can be
added here without changing callers.  See `sleep_with_jitter` for the
canonical gateway retry delay.
"""

from __future__ import annotations

import asyncio

__all__ = ["sleep_with_jitter"]

async def sleep_with_jitter(attempt: int, *, base: float = 0.05, jitter: float = 0.15) -> None:
    """Sleep for a short duration with simple deterministic jitter.

    The delay is computed as ``base + jitter * (attempt % 3)`` which
    produces a repeating sequence of three distinct backoff intervals.
    The ``attempt`` parameter should be the current retry count (starting
    from 1).  By moving the jitter logic into this helper the overall
    gateway retry strategy becomes easier to audit and adjust.

    Args:
        attempt: The current retry attempt (1-indexed).
        base: The minimum delay in seconds before retrying.  Defaults to 0.05.
        jitter: A multiplier used to add jitter based on the attempt
            number.  Defaults to 0.15.
    """
    if attempt < 1:
        attempt = 1
    delay = base + jitter * (attempt % 3)
    await asyncio.sleep(delay)