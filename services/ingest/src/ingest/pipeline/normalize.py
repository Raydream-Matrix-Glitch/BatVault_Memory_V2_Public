import re, unicodedata
from datetime import datetime, timezone
from dateutil import parser as dtp

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$")

def slugify_id(s: str) -> str:
    s = unicodedata.normalize("NFKC", s.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

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
    out["tags"] = sorted(set(t.lower() for t in d.get("tags", [])))
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
    out["tags"] = sorted(set(t.lower() for t in e.get("tags", [])))
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
    out["tags"] = sorted(set(x.lower() for x in t.get("tags", [])))
    return out

def derive_backlinks(decisions: dict, events: dict, transitions: dict) -> None:
    """
    Ensure:
      - event.led_to ↔ decision.supported_by
      - transitions appear in both decisions' transitions[] (by id)
    Only enforce referential integrity when arrays are non-empty (or field present & non-empty).
    """
    # Events -> Decisions
    for eid, e in events.items():
        for did in e.get("led_to", []):
            if did in decisions:
                dec = decisions[did]
                sb = set(dec.get("supported_by", []))
                if eid not in sb:
                    sb.add(eid)
                    dec["supported_by"] = sorted(sb)

    # Transitions in both decisions
    for tid, tr in transitions.items():
        fr, to = tr["from"], tr["to"]
        if fr in decisions:
            lst = set(decisions[fr].get("transitions", []))
            lst.add(tid); decisions[fr]["transitions"] = sorted(lst)
        if to in decisions:
            lst = set(decisions[to].get("transitions", []))
            lst.add(tid); decisions[to]["transitions"] = sorted(lst)
