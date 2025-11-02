"""
Pure text helpers (deterministic, no side effects).

Baseline alignment:
- Lives in core_utils (A.1) and is safe for read-time services.  :contentReference[oaicite:3]{index=3}
- Replaces any read-time usage of `shared.*` which is ingest-only.  :contentReference[oaicite:4]{index=4}
"""
from __future__ import annotations
from typing import Mapping, Any, Tuple, Optional, Iterable

# Deterministic field preference order (left→right)
_PRIMARY_TEXT_FIELDS: Tuple[str, ...] = (
    "text",
    "content",
    "description",
    "summary",
    "title",
)

def _first_nonempty_str(values: Iterable[tuple[str, Any]]) -> tuple[str, Optional[str]]:
    """
    Return the first (value, key) where value is a non-empty string.
    Deterministic: consumes in given order.
    """
    for k, v in values:
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s, k
    return "", None

def primary_text_and_field(candidate: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    """
    Extract the primary textual content from a candidate item.

    Returns:
        (text, field_name_or_None)

    Selection is deterministic:
      1) Prefer known texty fields in a fixed order.
      2) Fallback to the first string-valued field in **lexicographic key order**.
      3) If none found, return ("", None).
    """
    if not isinstance(candidate, Mapping):
        return "", None

    # 1) Preferred fields in fixed order
    ordered = ((f, candidate.get(f)) for f in _PRIMARY_TEXT_FIELDS)
    text, field = _first_nonempty_str(ordered)
    if text:
        return text, field

    # 2) Fallback: first string field by sorted key to guarantee determinism
    sorted_items = ((k, candidate.get(k)) for k in sorted(candidate.keys()))
    text, field = _first_nonempty_str(sorted_items)
    return text, field

def primary_text(candidate: Mapping[str, Any]) -> str:
    """
    Backwards-compatible convenience wrapper that returns only the resolved text.
    Mirrors the deterministic selection used by `primary_text_and_field`.
    """
    text, _ = primary_text_and_field(candidate)
    return text
