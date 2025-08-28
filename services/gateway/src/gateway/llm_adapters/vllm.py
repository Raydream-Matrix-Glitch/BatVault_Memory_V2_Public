"""
Adapter for vLLM inference endpoints.

This adapter talks to the OpenAI-compatible API exposed by vLLM.  It
enforces a strict JSON-only response by specifying the ``response_format``
parameter and injecting a system prompt.  When the endpoint responds, the
``choices[0].message.content`` field is returned as-is.  Callers are
responsible for validating the final JSON.
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Optional

import httpx
import orjson

from gateway.prompt_messages import build_messages
from core_logging import trace_span

def _inject_trace_context(headers: dict | None = None) -> dict:
    h = dict(headers or {})
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(h)
    except Exception:
        pass
    return h

def generate(
    endpoint: str,
    envelope: Dict[str, Any],
    *,
    temperature: float = 0.0,
    max_tokens: int = 512,
    messages: list[dict] | None = None,
) -> str:
    """
    Generate a summary using a vLLM endpoint.

    Parameters
    ----------
    endpoint: str
        Base URL of the vLLM API (e.g. "http://vllm-control:8000").
    envelope: dict
        Prompt envelope to summarise.  Will be serialised via JSON.
    temperature: float
        Sampling temperature for generation.
    max_tokens: int
        Maximum tokens to generate in the response.

    Returns
    -------
    str
        Raw JSON string from the model.

    Raises
    ------
    Exception
        If the endpoint fails or returns non-JSON output.
    """
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    # Compose messages via shared helper; but honor an explicit messages argument from the gate.
    payload_messages = messages if messages is not None else build_messages(envelope)
    payload = {
        "model": endpoint.split("/")[-1],
        "messages": payload_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Instruct vLLM to emit JSON without code fences
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=30.0) as client:
        with trace_span("gateway.llm.http", stage="llm") as sp:
            try:
                sp.set_attribute("endpoint", url)
                sp.set_attribute("model", payload.get("model"))
                sp.set_attribute("temperature", float(temperature))
                sp.set_attribute("max_tokens", int(max_tokens))
            except Exception:
                pass
            resp = client.post(url, json=payload, headers=_inject_trace_context({}))
        resp.raise_for_status()
        data = resp.json()
        try:
            raw = data["choices"][0]["message"]["content"]
        except Exception:
            raise ValueError("Unexpected vLLM response schema")
        # Validate minimal JSON parse – raises if malformed
        json.loads(raw)
        return raw