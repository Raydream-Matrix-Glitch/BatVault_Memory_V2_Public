import re
import hashlib
from typing import Dict
from core_logging import get_logger, log_stage, current_request_id

logger = get_logger("ingest-snippet")

_MAX_LEN = 160

def _normalize_whitespace(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip())
    return s or None

def _clip(s: str, max_len: int = _MAX_LEN) -> str:
    s = _normalize_whitespace(s) or ""
    return s[:max_len]

def _mk_snippet_id(kind: str, node_id: str, snippet: str) -> str:
    raw = f"{kind}|{node_id}|{snippet}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def enrich_decision(doc: Dict) -> None:
    parts = [doc.get("title"), doc.get("summary"), doc.get("rationale"), doc.get("option")]
    text = " ".join([p for p in parts if p])
    snip = _clip(text)
    if snip:
        doc.setdefault("x-extra", {})["snippet"] = snip
        log_stage(
            logger, "ingest", "snippet_created",
                  node_type="decision", node_id=doc.get("id"),
                  snippet_id=_mk_snippet_id("decision", doc.get("id",""), snip),
                  length=len(snip), request_id=(current_request_id() or "unknown"),
        )

def enrich_event(doc: Dict) -> None:
    parts = [doc.get("title"), doc.get("summary"), doc.get("description")]
    text = " ".join([p for p in parts if p])
    snip = _clip(text)
    if snip:
        doc.setdefault("x-extra", {})["snippet"] = snip
        log_stage(
            logger, "ingest", "snippet_created",
                  node_type="event", node_id=doc.get("id"),
                  snippet_id=_mk_snippet_id("event", doc.get("id",""), snip),
                  length=len(snip), request_id=(current_request_id() or "unknown"),
        )

def enrich_all(decisions: Dict[str, dict], events: Dict[str, dict]) -> None:
    for d in decisions.values():
        enrich_decision(d)
    for e in events.values():
        enrich_event(e)