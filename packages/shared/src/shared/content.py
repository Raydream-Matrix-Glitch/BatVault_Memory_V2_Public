"""
Schema-agnostic content access helpers.

Centralises the logic for picking the primary text field from a mixed
document so Gateway and other services don't hard-code field names.
"""
from typing import Any, Dict, Tuple, Optional

_PREFERRED_FIELDS = (
    # Reasoning/explanatory content first
    "rationale",
    # Richer descriptions then summaries/snippets
    "description", "summary", "content", "text", "body", "snippet",
   # Titles last (short)
    "title", "option",
)

def primary_text_and_field(doc: Dict[str, Any]) -> Tuple[str, str]:
    """
    Return (text, field) chosen as the primary textual content for *doc*.
    Empty strings are treated as missing. If no field matches, returns ("", "").
    """
    for k in _PREFERRED_FIELDS:
        v = doc.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip(), k
    return "", ""

def primary_text(doc: Dict[str, Any]) -> str:
    """
    Convenience wrapper around primary_text_and_field returning only the text.
    """
    v, _ = primary_text_and_field(doc)
    return v
