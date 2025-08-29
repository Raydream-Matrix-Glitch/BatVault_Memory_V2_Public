from __future__ import annotations
import re
import html
import hashlib
from typing import Iterable, Optional, Dict, Any

from core_logging import get_logger, log_stage
from shared.content import primary_text

logger = get_logger("gateway.snippet")

_MAX = 160
_BEFORE = 70
_AFTER = 90

def _norm_ws(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return re.sub(r"\s+", " ", str(s)).strip()

def _terms(q: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", q.lower()) if t]

def _mk_id(match_id: str, q: str, snippet: str) -> str:
    h = hashlib.sha256()
    h.update((match_id + "|" + q + "|" + snippet).encode("utf-8"))
    return "sha256:" + h.hexdigest()

def _window(s: str, i: int, j: int) -> str:
    start = max(0, i - _BEFORE)
    end = min(len(s), j + _AFTER)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(s) else ""
    return prefix + s[start:end] + suffix

def make_snippet(query: str, match: Dict[str, Any]) -> str:
    """Build a short HTML-safe snippet highlighting a match.

    * Uses the primary text from the match (normalized by shared.content.primary_text)
    * Prefers the first matching term but falls back to a simple head clip
    * Emits structured log for audit with a deterministic snippet_id
    """
    q = _norm_ws(query) or ""
    src = _norm_ws(primary_text(match)) or ""
    if not src:
        return ""  # nothing to show

    ts = _terms(q)
    if not ts:
        return html.escape(src[:_MAX] + ("…" if len(src) > _MAX else ""), quote=False)

    # Find first term occurrence
    m = None
    for t in ts:
        m = re.search(re.escape(t), src, flags=re.IGNORECASE)
        if m:
            break

    if not m:
        snippet = src[:_MAX] + ("…" if len(src) > _MAX else "")
    else:
        snippet = _window(src, m.start(), m.end())

    safe = html.escape(snippet, quote=False)
    match_id = str(match.get("id") or match.get("_id") or "")
    log_stage(
        logger, "gateway", "match_snippet_created",
        match_id=match_id,
        snippet_id=_mk_id(match_id, q, safe),
        q_terms=ts,
        length=len(safe),
    )
    return safe