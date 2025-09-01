"""
Adapter for vLLM-style OpenAI-compatible chat endpoints.
Minimal by design: prompt construction lives in `gateway.prompt_messages`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core_config.constants import timeout_for_stage
from ..prompt_messages import build_messages
from core_http.client import get_http_client


def _build_payload(envelope: Dict[str, Any], *, temperature: float, max_tokens: int) -> Dict[str, Any]:
    msgs: List[Dict[str, str]] = build_messages(envelope)
    return {
        "model": envelope.get("policy", {}).get("model") or envelope.get("intent") or "default",
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "messages": msgs,
    }

async def generate_async(endpoint: str, envelope: Dict[str, Any], *, temperature: float = 0.0, max_tokens: int = 512) -> str:
    """Async variant using the shared HTTP client (OTEL/headers applied)."""
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    payload = _build_payload(envelope, temperature=temperature, max_tokens=max_tokens)
    client = get_http_client(timeout_ms=int(timeout_for_stage('llm')*1000))
    r = await client.post(url, json=payload)
    r.raise_for_status()
    data = r.json()
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")