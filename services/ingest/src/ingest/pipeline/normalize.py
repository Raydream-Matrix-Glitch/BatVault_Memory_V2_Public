import re
from datetime import datetime, timezone
from dateutil import parser as dtp
from core_utils import slugify_id
from link_utils import derive_links
import unicodedata

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")

def norm_timestamp(ts: str) -> str:
    dt = dtp.parse(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def norm_text(s: str | None, max_len: int | None = None) -> str | None:
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", s).strip()
    s = re.sub(r"\s+", " ", s)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s

def normalize_decision(d: dict) -> dict:
    out = dict(d)
    out["id"] = d["id"] if ID_RE.match(d["id"]) else slugify_id(d["id"])
    out["option"] = norm_text(d.get("option"), 300)
    out["rationale"] = norm_text(d.get("rationale"), 600)
    out["timestamp"] = norm_timestamp(d["timestamp"])
    out["decision_maker"] = norm_text(d.get("decision_maker"), 120)
    out["tags"] = normalize_tags(d.get("tags", []))
    for k in ("supported_by","based_on","transitions"):
        arr = d.get(k) or []
        if not isinstance(arr, list): arr = []
        out[k] = [str(x) for x in arr]
    return out

def normalize_event(e: dict) -> dict:
    out = dict(e)
    out["id"] = e["id"] if ID_RE.match(e["id"]) else slugify_id(e["id"])
    out["timestamp"] = norm_timestamp(e["timestamp"])
    out["summary"] = norm_text(e.get("summary"), 120)
    out["description"] = norm_text(e.get("description"))
    # summary repair if missing/empty or equals id
    if not out.get("summary") or out["summary"] == out["id"]:
        desc = out.get("description") or ""
        out["summary"] = norm_text(desc[:96]) or "(no-summary)"
    # simple snippet = first sentence up to 160 chars
    if not e.get("snippet"):
        desc = out.get("description") or ""
        first = desc.split(".")[0][:160]
        out["snippet"] = norm_text(first, 160)
    out["tags"] = normalize_tags(e.get("tags", []))
    arr = e.get("led_to") or []
    out["led_to"] = [str(x) for x in arr] if isinstance(arr, list) else []
    return out

def normalize_transition(t: dict) -> dict:
    out = dict(t)
    out["id"] = t["id"] if ID_RE.match(t["id"]) else slugify_id(t["id"])
    out["from"] = str(t["from"])
    out["to"] = str(t["to"])
    out["relation"] = t.get("relation") or "causal"
    out["reason"] = norm_text(t.get("reason"), 280)
    out["timestamp"] = norm_timestamp(t["timestamp"])
    out["tags"] = normalize_tags(t.get("tags", []))
    return out

def normalize_tags(tags: list[str]) -> list[str]:
    """Canonical tag normalization – spec §L2: slug-lower, dedupe, sort."""
    normalized = [slugify_id(t) for t in tags]
    return sorted(set(normalized))

def derive_backlinks(decisions: dict, events: dict, transitions: dict) -> None:
    """Shim: delegates to link_utils.derive_links for reciprocity."""
    return derive_links(decisions, events, transitions)
