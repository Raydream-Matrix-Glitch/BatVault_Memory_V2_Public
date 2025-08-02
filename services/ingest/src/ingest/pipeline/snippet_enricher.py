import re
import hashlib
from typing import Dict
from core_logging import get_logger, log_stage

logger = get_logger("ingest-snippet")

_MAX_LEN = 160

def _normalize_whitespace(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip())
    return s or None

def _clip(s: str, max_len: int = _MAX_LEN) -> str:
    if len(s) <= max_len:
        return s
    cut = s.rfind(" ", 0, max_len)
    if cut < 40:  # avoid ultra-short hard cut
        cut = max_len
    return s[:cut].rstrip() + "…"

def _mk_snippet_id(node_type: str, node_id: str, snippet: str) -> str:
    raw = f"{node_type}:{node_id}:{snippet}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]

def enrich_decision(dec: dict) -> None:
    if dec.get("snippet"):
        return
    base = _normalize_whitespace(dec.get("rationale")) or _normalize_whitespace(dec.get("option"))
    if not base:
        return
    snip = _clip(base)
    dec["snippet"] = snip
    log_stage(
        logger, "ingest", "snippet_created",
        node_type="decision", node_id=dec.get("id"),
        snippet_id=_mk_snippet_id("decision", dec.get("id", ""), snip),
        length=len(snip)
    )

def enrich_event(ev: dict) -> None:
    if ev.get("snippet"):
        return
    base = _normalize_whitespace(ev.get("summary")) or _normalize_whitespace(ev.get("description"))
    if not base:
        return
    snip = _clip(base)
    ev["snippet"] = snip
    log_stage(
        logger, "ingest", "snippet_created",
        node_type="event", node_id=ev.get("id"),
        snippet_id=_mk_snippet_id("event", ev.get("id", ""), snip),
        length=len(snip)
    )

def enrich_transition(tr: dict) -> None:
    if tr.get("snippet"):
        return
    base = _normalize_whitespace(tr.get("reason"))
    if not base:
        # keep it intentionally terse and non-identifying
        base = _normalize_whitespace(tr.get("relation")) or "transition"
    snip = _clip(base)
    tr["snippet"] = snip
    log_stage(
        logger, "ingest", "snippet_created",
        node_type="transition", node_id=tr.get("id"),
        snippet_id=_mk_snippet_id("transition", tr.get("id", ""), snip),
        length=len(snip)
    )

def enrich_all(decisions: Dict[str, dict], events: Dict[str, dict], transitions: Dict[str, dict]) -> None:
    for d in decisions.values():
        enrich_decision(d)
    for e in events.values():
        enrich_event(e)
    for t in transitions.values():
        enrich_transition(t)
