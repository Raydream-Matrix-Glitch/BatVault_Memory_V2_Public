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
from typing import Dict, Any, List, Optional

import httpx
import orjson

from core_logging import get_logger, log_stage, trace_span
from core_metrics import counter as metric_counter, histogram as metric_histogram

from .llm_adapters import vllm as _vllm_adapter  # type: ignore
from .llm_adapters import tgi as _tgi_adapter    # type: ignore

from core_config.constants import CONTROL_CONTEXT_WINDOW, CONTROL_PROMPT_GUARD_TOKENS
from shared.tokens import estimate_messages_tokens
from gateway.prompt_messages import build_messages

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
    messages_override: Optional[List[Dict[str, str]]] = None,
    max_tokens_override: Optional[int] = None,
) -> str:
    """
    Invoke the control or canary LLM adapter based on a stable hash and
    return the resulting JSON string.  On failure, retries with jitter
    up to *retries* times before raising.  Temperature and max tokens are
    configured via environment variables.
    """
    # Determine routing target
    # Hard disable if CANARY_ENABLED=0 or CANARY_PCT<=0
    canary_enabled = os.getenv("CANARY_ENABLED", "1") == "1"
    if not canary_enabled or int(os.getenv("CANARY_PCT", "0")) <= 0:
        use_canary = False
        try:
            log_stage(
                logger, "llm", "canary_disabled",
                request_id=request_id,
                reason="env_gate",
                canary_enabled=canary_enabled,
                canary_pct=os.getenv("CANARY_PCT", "0"),
            )
        except Exception:
            pass
    else:
        use_canary = _should_use_canary(request_id, headers)
    canary_ep  = os.getenv("CANARY_MODEL_ENDPOINT", "http://tgi-canary:8090")
    control_ep = os.getenv("CONTROL_MODEL_ENDPOINT", "http://vllm-control:8010")
    model_endpoint = canary_ep if use_canary else control_ep
    model_name     = "canary" if use_canary else "control"
    with trace_span("gateway.llm.select", stage="llm") as sp:
        try:
            sp.set_attribute("canary", use_canary)
            sp.set_attribute("endpoint", model_endpoint)
            sp.set_attribute("model", model_name)
        except Exception:
            pass
    # Strategic log: route decision
    try:
        log_stage(
            logger, "llm", "route",
            request_id=request_id,
            canary=use_canary,
            endpoint=model_endpoint,
            model=model_name,
            retries=retries,
        )
    except Exception:
        pass
 
    # Router does not re-budget; gate sets desired completion. We only clamp safely.
    temp = float(os.getenv("LLM_TEMPERATURE", "0"))
    planned_completion = (
        int(max_tokens_override) if isinstance(max_tokens_override, int)
        else int(os.getenv("LLM_MAX_TOKENS", "512"))
    )
    global last_call
    exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        # no external timing here; latency is measured within the llm.call span
        try:
            # Compute clamp using final messages (override or builder-rendered)
            messages = messages_override if messages_override is not None else build_messages(envelope)
            try:
                prompt_tokens = int(estimate_messages_tokens(messages))
            except Exception:
                prompt_tokens = 0
            remaining = max(0, CONTROL_CONTEXT_WINDOW - prompt_tokens - CONTROL_PROMPT_GUARD_TOKENS)
            max_tokens = max(1, min(planned_completion, remaining))
            if max_tokens < planned_completion:
                try:
                    log_stage(
                        logger, "router", "safety_clamp",
                        request_id=request_id,
                        prompt_tokens=prompt_tokens,
                        planned_max=planned_completion,
                        final_max=max_tokens,
                    )
                except Exception:
                    pass

            # Choose adapter based on target (adapters remain unchanged).
            # Adapters will render messages from the envelope; since builder updated
            # the envelope with the gate-trimmed evidence, rendering is identical to the gate.
            with trace_span("gateway.llm.call", stage="llm", request_id=request_id) as call_span:
                # Set per-call attributes on the llm.call span.  Attaching these attributes
                # here ensures the inference call’s metadata is associated with the correct span.
                try:
                    call_span.set_attribute("model", model_name)
                    call_span.set_attribute("temperature", float(os.getenv("LLM_TEMPERATURE", "0.0")))
                    call_span.set_attribute("max_tokens", max_tokens)
                    call_span.set_attribute("planned_completion", planned_completion)
                    call_span.set_attribute("prompt_tokens", prompt_tokens)
                    call_span.set_attribute("context_window", CONTROL_CONTEXT_WINDOW)
                    call_span.set_attribute("canary", use_canary)
                except Exception:
                    pass
                # Measure latency within the span to expose it as an attribute and exemplar
                _call_t0 = time.perf_counter()
                if use_canary:
                    # TGI path (string prompt). messages_override unused by adapter.
                    raw = _tgi_adapter.generate(
                        model_endpoint, envelope, temperature=temp, max_tokens=max_tokens, messages=messages_override
                    )
                else:
                    # vLLM path (chat). Use gate-rendered messages when provided.
                    raw = _vllm_adapter.generate(
                        model_endpoint, envelope, temperature=temp, max_tokens=max_tokens, messages=messages_override
                    )
                dt_ms = int((time.perf_counter() - _call_t0) * 1000)
                try:
                    call_span.set_attribute("latency_ms", dt_ms)
                except Exception:
                    pass
            # `dt_ms` is defined in the span above; use it for metrics and audit
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
                    llm_temperature=temp,
                    llm_max_tokens=max_tokens,
                    vllm_gpu_util=os.getenv("VLLM_GPU_UTIL"),
                    vllm_max_model_len=os.getenv("VLLM_MAX_MODEL_LEN"),
                    vllm_max_num_seqs=os.getenv("VLLM_MAX_NUM_SEQS"),
                    vllm_max_batched_tokens=os.getenv("VLLM_MAX_BATCHED_TOKENS"),
                )
            except Exception:
                pass
            return raw
        except Exception as err:
            exc = err
            # If canary failed on first try: log + retry once on control backend
            if use_canary and attempt == 0:
                try:
                    log_stage(
                        logger, "llm", "canary_failed_retry_control",
                        request_id=request_id,
                        error=type(err).__name__,
                        canary_endpoint=model_endpoint,
                        control_endpoint=control_ep,
                    )
                except Exception:
                    pass
                # reroute to control for the remaining attempts
                use_canary     = False
                model_endpoint = control_ep
                model_name     = "control"
                # continue loop without sleeping so we immediately try control
                continue
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