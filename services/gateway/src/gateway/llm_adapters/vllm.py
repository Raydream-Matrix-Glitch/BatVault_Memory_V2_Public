"""
Adapter for vLLM-style OpenAI-compatible chat endpoints.
Minimal by design: prompt construction lives in `gateway.prompt_messages`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core_config.constants import timeout_for_stage
from ..prompt_messages import build_messages
from core_http.client import get_http_client
from core_observability.otel import inject_trace_context
from core_config import get_settings

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
    client = get_http_client(timeout_ms=int(timeout_for_stage('llm')*1000))
    r = await client.post(url, json=payload, headers=inject_trace_context({}))
    r.raise_for_status()
    data = r.json()
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped.strip("`").strip()
    return text