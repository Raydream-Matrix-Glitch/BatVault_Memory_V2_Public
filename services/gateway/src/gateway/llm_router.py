"""This module centralises the decision of which inference endpoint to call
(control vs canary) and wraps the HTTP invocation with retries,
jitter and structured logging.  The selection is stable per request
identifier: a simple hash determines whether a given call falls into
the canary cohort based on the configured percentage.  Callers may
override the selection via a dedicated header (e.g. ``x-batvault-canary``).

Public function
----------------
``call_llm(envelope: dict, request_id: str | None = None,
          headers: dict[str, str] | None = None) -> str``
    Invoke the chosen model adapter to obtain a JSON string.  The
    temperature and max token parameters are read from environment
    variables (``LLM_TEMPERATURE`` and ``LLM_MAX_TOKENS``).  The
    wrapper automatically records metrics and exposes the last
    invocation details via the module-level ``last_call`` dict.

##Environment variables
---------------------
CONTROL_MODEL_ENDPOINT``: URL of the control inference endpoint (vLLM)
CANARY_MODEL_ENDPOINT``: URL of the canary inference endpoint (TGI)
CANARY_PCT``: integer 0–100 controlling what fraction of requests go to the canary
CANARY_HEADER_OVERRIDE``: HTTP header name; if present in the incoming
                           request headers, forces canary routing
LLM_TEMPERATURE``: float controlling generation randomness (defaults to 0.0)
LLM_MAX_TOKENS``: integer controlling maximum tokens in the response
"""

from __future__ import annotations

import os
import time
import hashlib
import random
from typing import Any, Dict, Optional

import httpx

from core_logging import get_logger, log_stage
from core_metrics import counter as metric_counter, histogram as metric_histogram

from .llm_adapters import vllm as _vllm_adapter  # type: ignore
from .llm_adapters import tgi as _tgi_adapter    # type: ignore

logger = get_logger("gateway.llm_router")
logger.propagate = True

# Persist metadata of the last invocation; read by the Gateway to attach
# audit headers and metrics.  Keys: model (str), canary (bool), latency_ms (int).
last_call: Dict[str, Any] = {}


def _stable_hash_int(s: str) -> int:
    """Return a deterministic integer in [0, 99] derived from the SHA256 of *s*."""
    h = hashlib.sha256(s.encode("utf-8")).digest()
    # Use the first two bytes to form a 16-bit integer then mod 100
    return (h[0] << 8 | h[1]) % 100


def _should_use_canary(request_id: Optional[str], headers: Optional[Dict[str, str]]) -> bool:
    """Determine whether this request should be routed to the canary model."""
    override_hdr = os.getenv("CANARY_HEADER_OVERRIDE", "").lower()
    canary_pct = int(os.getenv("CANARY_PCT", "0"))
    # Header override takes priority
    if headers and override_hdr and override_hdr in {k.lower() for k in headers.keys()}:
        return True
    if not request_id:
        return False
    try:
        val = _stable_hash_int(request_id)
    except Exception:
        return False
    return val < canary_pct


def call_llm(
    envelope: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = 2,
) -> str:
    """
    Invoke the control or canary LLM adapter based on a stable hash and
    return the resulting JSON string.  On failure, retries with jitter
    up to *retries* times before raising.  Temperature and max tokens are
    configured via environment variables.
    """
    # Determine routing target
    use_canary = _should_use_canary(request_id, headers)
    model_endpoint = (
        os.getenv("CANARY_MODEL_ENDPOINT", "http://tgi-canary:8080")
        if use_canary
        else os.getenv("CONTROL_MODEL_ENDPOINT", "http://vllm-control:8000")
    )
    model_name = "canary" if use_canary else "control"
    # Temperature/max tokens defaults
    temp = float(os.getenv("LLM_TEMPERATURE", "0"))
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "512"))
    global last_call
    exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            # Choose adapter based on target
            if use_canary:
                raw = _tgi_adapter.generate(
                    model_endpoint, envelope, temperature=temp, max_tokens=max_tokens
                )
            else:
                raw = _vllm_adapter.generate(
                    model_endpoint, envelope, temperature=temp, max_tokens=max_tokens
                )
            dt_ms = int((time.perf_counter() - t0) * 1000)
            # Record last call metadata for audit/SSE headers
            last_call = {
                "model": model_name,
                "canary": use_canary,
                "latency_ms": dt_ms,
            }
            # Metrics: record successful call
            try:
                metric_counter(
                    "gateway_llm_requests",
                    1,
                    model=model_name,
                    canary=str(use_canary).lower(),
                )
                metric_histogram(
                    "gateway_llm_latency_ms",
                    float(dt_ms),
                    model=model_name,
                    canary=str(use_canary).lower(),
                )
            except Exception:
                pass
            # Structured log for successful completion
            try:
                log_stage(
                    logger,
                    "llm",
                    "success",
                    request_id=request_id,
                    model=model_name,
                    canary=use_canary,
                    latency_ms=dt_ms,
                )
            except Exception:
                pass
            return raw
        except Exception as err:
            exc = err
            # Structured log for failure
            try:
                log_stage(
                    logger,
                    "llm",
                    "error",
                    request_id=request_id,
                    model=model_name,
                    canary=use_canary,
                    attempt=attempt,
                    error=type(err).__name__,
                )
            except Exception:
                pass
            # simple jitter: random delay up to 100ms
            time.sleep(0.05 + random.random() * 0.05)
    # Out of retries: re-raise last exception
    raise exc  # type: ignore[misc]