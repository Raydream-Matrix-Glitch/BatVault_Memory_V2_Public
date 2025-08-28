from __future__ import annotations

# Lightweight heuristic: ~4 chars/token. Pure Python to avoid heavy deps.
_CHARS_PER_TOKEN = 4.0
_MIN_OVERHEAD_TOKENS = 16  # system/role/stop overhead buffer

def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # Pure content estimate; no global overhead here.
    return max(int(len(text) / _CHARS_PER_TOKEN), 0)

def estimate_messages_tokens(messages: list[dict]) -> int:
    """
    messages: [{"role": "...","content": "..."}] (OpenAI-style)
    """
    if not messages:
        return _MIN_OVERHEAD_TOKENS
    # Apply a single global overhead buffer once per prompt.
    total = _MIN_OVERHEAD_TOKENS
    for m in messages:
        c = m.get("content") or ""
        total += estimate_text_tokens(str(c)) + 4  # small per-message overhead
    return total