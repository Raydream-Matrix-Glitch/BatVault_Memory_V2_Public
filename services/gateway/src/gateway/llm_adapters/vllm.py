"""
Adapter for vLLM-style OpenAI-compatible chat endpoints.
Minimal by design: prompt construction lives in `gateway.prompt_messages`.
"""
from __future__ import annotations

from typing import Any, Dict, List
import os

from core_config.constants import timeout_for_stage
from ..prompt_messages import build_messages
from core_http.client import get_http_client
from core_observability.otel import inject_trace_context
from core_config import get_settings
from ..logging_helpers import stage as log_stage

def __get_model_name__() -> str:
    """Resolve the model name to send to vLLM.

    Prefer the envelope.policy.model; otherwise fall back to settings.vllm_model_name.
    As a last resort, return 'default' which vLLM accepts but some builds may
    validate; keeping this here maintains backward-compatibility.
    """
    try:
        name = (get_settings().vllm_model_name or '').strip()
        return name or 'default'
    except Exception:
        return 'default'

def _compute_timeout_ms(max_tokens: int) -> int:
    """Compute HTTP read-timeout for the vLLM call (env-driven).

    Base comes from TIMEOUT_LLM_MS via `timeout_for_stage('llm')` (seconds → ms).
    Optional dynamic scaling (enabled only if both vars > 0):
      - LLM_TIMEOUT_BASE_MS
      - LLM_TIMEOUT_PER_100TOK_MS
    Effective timeout:
        max(stage_ms, LLM_TIMEOUT_BASE_MS + ceil(max_tokens/100)*LLM_TIMEOUT_PER_100TOK_MS)
    """
    try:
        stage_ms = int(timeout_for_stage('llm') * 1000)
    except Exception:
        stage_ms = 6000
    base_ms = int(os.getenv("LLM_TIMEOUT_BASE_MS", "0"))
    per_100 = int(os.getenv("LLM_TIMEOUT_PER_100TOK_MS", "0"))
    if base_ms > 0 and per_100 > 0:
        blocks = (int(max_tokens) + 99) // 100
        return max(stage_ms, base_ms + blocks * per_100)
    return stage_ms

def _build_payload(envelope: Dict[str, Any], *, temperature: float, max_tokens: int) -> Dict[str, Any]:
    msgs: List[Dict[str, str]] = build_messages(envelope)
    return {
        "model": (envelope.get("policy", {}) or {}).get("model") or __get_model_name__(),
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "messages": msgs,
    }

async def generate_async(endpoint: str, envelope: Dict[str, Any], *, temperature: float = 0.0, max_tokens: int = 512) -> str:
    """Async variant using the shared HTTP client (OTEL/headers applied)."""
    base = endpoint.rstrip("/")
    # Avoid double "/v1" if users pass CONTROL_MODEL_ENDPOINT ending with "/v1"
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    elif base.endswith("/v1/chat/completions"):
        url = base
    else:
        url = base + "/v1/chat/completions"
    payload = _build_payload(envelope, temperature=temperature, max_tokens=max_tokens)
    timeout_ms = _compute_timeout_ms(max_tokens)
    try:
        log_stage("inference", "dispatch_timeout",
                  timeout_ms=timeout_ms, endpoint=base, max_tokens=int(max_tokens))
    except Exception:
        # Never allow logging to interfere with the hot path
        pass
    client = get_http_client(timeout_ms=timeout_ms)
    r = await client.post(url, json=payload, headers=inject_trace_context({}))
    r.raise_for_status()
    data = r.json()
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped.strip("`").strip()
    return text