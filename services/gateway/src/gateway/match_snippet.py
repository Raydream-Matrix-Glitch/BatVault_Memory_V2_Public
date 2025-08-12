import re
import html
import hashlib
from typing import Iterable, Optional, Dict, Any
from core_logging import get_logger, log_stage
from shared.content import primary_text

logger = get_logger("gateway-snippet")

_MAX = 160
_BEFORE = 70
_AFTER = 90

def _norm_ws(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return re.sub(r"\s+", " ", s.strip())

def _best_source(m: Dict[str, Any]) -> Optional[str]:
    txt = primary_text(m)
    return txt or None

def _terms(query: str) -> Iterable[str]:
    q = _norm_ws(query) or ""
    # Keep alphanumerics and dashes/underscores (to match your ID/token rules)
    toks = re.findall(r"[A-Za-z0-9_-]{2,}", q.lower())
    # simple dedupe by length preference (longer first)
    toks = sorted(set(toks), key=len, reverse=True)
    return toks[:8]  # cap to keep regex small and deterministic

def _window(txt: str, start: int, end: int) -> str:
    L = max(0, start - _BEFORE)
    R = min(len(txt), end + _AFTER)
    snippet = txt[L:R]
    prefix = "…" if L > 0 else ""
    suffix = "…" if R < len(txt) else ""
    out = f"{prefix}{snippet}{suffix}"
    if len(out) <= _MAX:
        return out
    return (out[:_MAX - 1] + "…")

def _mk_id(match_id: str, query: str, snippet: str) -> str:
    raw = f"{match_id}|{query}|{snippet}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]

def build_match_snippet(match: Dict[str, Any], query: str) -> Optional[str]:
    src = _best_source(match)
    if not src:
        # -------------------- Contract fallback strategy -------------------- #
        # ❶ Derive a readable phrase from the slug-like ID when no content
        #    fields are available (e.g. tests that stub only {"id": "..."}).
        slug = match.get("id") or match.get("_id")
        if isinstance(slug, str):
            slug_src = _norm_ws(re.sub(r"[_-]+", " ", slug))
            if slug_src:
                src = slug_src

    if not src:
        # ❷ Absolute last resort — use the query itself so the “match_snippet”
        #    field is never omitted (M3 contract; still valid for M4).
        src = _norm_ws(query)

    if not src:
        return None
    src = _norm_ws(src) or ""
    if not src:
        return None

    ts = list(_terms(query))
    if not ts:
        # fall back to trimmed source
        return src[:_MAX] + ("…" if len(src) > _MAX else "")

    # build a single regex with alternatives, escaping tokens
    pat = re.compile("|".join(re.escape(t) for t in ts), flags=re.IGNORECASE)
    m = pat.search(src)
    if not m:
        return src[:_MAX] + ("…" if len(src) > _MAX else "")
    snippet = _window(src, m.start(), m.end())
    # HTML-safe to avoid leaking markup through SSE/JSON viewers
    safe = html.escape(snippet, quote=False)
    match_id = str(match.get("id") or match.get("_id") or "")
    log_stage(
        logger, "gateway", "match_snippet_created",
        match_id=match_id,
        snippet_id=_mk_id(match_id, query, safe),
        q_terms=ts,
        length=len(safe)
    )
    return safe
