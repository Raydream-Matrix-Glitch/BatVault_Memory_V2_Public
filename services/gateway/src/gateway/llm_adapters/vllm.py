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
from typing import Any, Dict

import httpx
import orjson


def generate(
    endpoint: str,
    envelope: Dict[str, Any],
    *,
    temperature: float = 0.0,
    max_tokens: int = 512,
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
    # Compose the system and user messages.  The system message instructs
    # the model to return a JSON object with the expected keys.
    messages = [
        {
            "role": "system",
            "content": (
                "You are a JSON-only assistant.  Given a prompt envelope, "
                "produce an object with two keys: short_answer (string) and "
                "supporting_ids (array of strings).  Do not include any extra "
                "fields or natural language commentary."
            ),
        },
        {
            "role": "user",
            "content": orjson.dumps(envelope).decode(),
        },
    ]
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Instruct vLLM to emit JSON without code fences
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        try:
            raw = data["choices"][0]["message"]["content"]
        except Exception:
            raise ValueError("Unexpected vLLM response schema")
        # Validate minimal JSON parse – raises if malformed
        json.loads(raw)
        return raw