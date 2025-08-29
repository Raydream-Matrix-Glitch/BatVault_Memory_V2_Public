"""
Adapter for Text Generation Inference (TGI) endpoints.
Minimal by design: prompt construction lives in `gateway.prompt_messages`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..prompt_messages import build_messages
from ..http import fetch_json

def _as_prompt(envelope: Dict[str, Any]) -> str:
    """TGI /generate accepts a single string prompt; join chat messages plainly."""
    msgs: List[Dict[str, str]] = build_messages(envelope)
    parts = []
    for m in msgs:
        role = m.get("role") or "user"
        parts.append(f"{role}:\n{m.get('content','')}")
    return "\n\n".join(parts)

async def generate_async(
    endpoint: str,
    envelope: Dict[str, Any],
    *,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> str:
    """Call TGI /generate and return `generated_text` (fences stripped). Stage timeout: llm."""
    url = endpoint.rstrip("/") + "/generate"
    payload = {
        "inputs": _as_prompt(envelope),
        "parameters": {"temperature": float(temperature), "max_new_tokens": int(max_tokens)},
    }
    # Use the unified fetch_json helper for consistent OTEL propagation,
    # retries and stage‑based timeouts.  TGI returns either a list of
    # completion objects or a dict with ``generated_text``.  Fallback to
    # stringification if the shape is unexpected.
    data = await fetch_json("POST", url, json=payload, stage="llm")
    text = ""
    if isinstance(data, list) and data:
        text = str((data[0] or {}).get("generated_text") or "")
    elif isinstance(data, dict):
        text = str(data.get("generated_text") or "")
    else:
        text = str(data)
    # Strip surrounding Markdown code fences if present to return raw text.
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped.strip("`").strip()
    return text