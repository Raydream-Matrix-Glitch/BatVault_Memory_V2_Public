"""
Adapter for Text Generation Inference (TGI) endpoints.

TGI exposes a ``/generate`` endpoint which accepts an input string and
generation parameters.  This adapter constructs a JSON-mode prompt,
calls the API synchronously and extracts the generated_text.  It
strips any Markdown code fences and validates that the result parses as
JSON before returning.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

import httpx
import orjson

from core_logging import trace_span


_JSON_FENCE_RE = re.compile(r"```(?:json)?\\s*(.*?)```", re.DOTALL)

def _inject_trace_context(headers: dict | None = None) -> dict:
    h = dict(headers or {})
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(h)
    except Exception:
        pass
    return h

def _extract_json(text: str) -> str:
    """Strip surrounding code fences if present and return the inner text."""
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def generate(
    endpoint: str,
    envelope: Dict[str, Any],
    *,
    temperature: float = 0.0,
    max_tokens: int = 512,
    messages: list[dict] | None = None,
) -> str:
    """
    Generate a summary via a TGI endpoint.

    Parameters
    ----------
    endpoint: str
        Base URL of the TGI service (e.g. "http://tgi-canary:8080").
    envelope: dict
        Prompt envelope.  Serialised to JSON and embedded in a structured
        system/user preface to coax a JSON-only response.
    temperature: float
        Sampling temperature.
    max_tokens: int
        Maximum new tokens to generate.

    Returns
    -------
    str
        Raw JSON string from the model.
    """
    url = endpoint.rstrip("/") + "/generate"
    # Compose a prompt that instructs the model to return only JSON.
    # TGI does not currently support the OpenAI response_format API.  We
    # prepend a system-like instruction before the JSON envelope.
    prompt = (
        "You are a JSON-only assistant.  Return a JSON object with keys "
        "short_answer (string) and supporting_ids (array of strings).\\n"
        f"Prompt envelope:\\n{orjson.dumps(envelope).decode()}"
    )
    payload = {
        "inputs": prompt,
        "parameters": {
            "temperature": temperature,
            "max_new_tokens": max_tokens,
            "return_full_text": False,
        },
    }
    with httpx.Client(timeout=30.0) as client:
        # Record a span for the HTTP call to the TGI endpoint.  Capture key
        # attributes from the payload rather than referencing an undefined
        # variable.  Use the prepared parameters dictionary so that
        # temperature and token limits are correctly attached to the span.
        with trace_span("gateway.llm.http", stage="llm") as sp:
            try:
                sp.set_attribute("endpoint", url)
                params = payload.get("parameters", {})
                if isinstance(params, dict):
                    temp_val = params.get("temperature")
                    max_new = params.get("max_new_tokens")
                    if temp_val is not None:
                        sp.set_attribute("temperature", float(temp_val))
                    if max_new is not None:
                        sp.set_attribute("max_tokens", int(max_new))
            except Exception:
                pass
            resp = client.post(url, json=payload, headers=_inject_trace_context({}))
        resp.raise_for_status()
        data = resp.json()
        try:
            raw_text = data["generated_text"]
        except Exception:
            raise ValueError("Unexpected TGI response schema")
        raw = _extract_json(raw_text)
        # Validate parse
        json.loads(raw)
        return raw