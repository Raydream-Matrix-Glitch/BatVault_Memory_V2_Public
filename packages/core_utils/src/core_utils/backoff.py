"""Utility routines for retry and jitter policies.

This module centralizes common backoff patterns (e.g., jittered sleeps)
so that services can depend on a single implementation.
"""

from __future__ import annotations
import asyncio
import os
from typing import Literal

__all__ = ["sleep_with_jitter", "compute_backoff_delay_ms", "async_backoff_sleep"]

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

def _rand_u8() -> int:
    """Small helper to avoid importing random; uses os.urandom."""
    return int.from_bytes(os.urandom(1), "big")

def compute_backoff_delay_ms(
    attempt: int,
    *,
    base_ms: int,
    jitter_ms: int,
    cap_ms: int | None = None,
    mode: Literal["exp_equal_jitter", "exp_full_jitter", "decorrelated"] = "exp_equal_jitter",
) -> int:
    """Compute a retry backoff (milliseconds) with jitter.

    Modes:
      - exp_equal_jitter: (base * 2**(n-1)) + uniform(0, jitter)
      - exp_full_jitter:  uniform(0, (base * 2**(n-1)) + jitter)
      - decorrelated:     min(cap, max(base, prev * 3 * uniform(0,1)))
    """
    if attempt < 1:
        attempt = 1
    exp = base_ms * (2 ** (attempt - 1))
    if mode == "exp_equal_jitter":
        jitter = _rand_u8() % max(1, jitter_ms)
        delay = exp + jitter
    elif mode == "exp_full_jitter":
        span = exp + max(1, jitter_ms)
        delay = int((_rand_u8() / 255.0) * span)
    else:  # decorrelated
        prev = base_ms * (2 ** max(0, attempt - 2))
        span = int(prev * 3)
        delay = max(base_ms, int((_rand_u8() / 255.0) * span))
    if cap_ms is not None:
        delay = min(delay, cap_ms)
    return max(0, int(delay))

async def async_backoff_sleep(
    attempt: int,
    *,
    base_ms: int,
    jitter_ms: int,
    cap_ms: int | None = None,
    mode: Literal["exp_equal_jitter", "exp_full_jitter", "decorrelated"] = "exp_equal_jitter",
) -> None:
    """Async sleep wrapper around compute_backoff_delay_ms."""
    delay_ms = compute_backoff_delay_ms(attempt, base_ms=base_ms, jitter_ms=jitter_ms, cap_ms=cap_ms, mode=mode)
    await asyncio.sleep(delay_ms / 1000.0)