import re
from datetime import datetime, timezone
from core_logging import get_logger, log_stage
from dateutil import parser as dtp
from core_utils import slugify_id, slugify_tag
from link_utils import derive_links
import unicodedata

# Import canonical normalisation routines from the shared package. These helpers
# enforce a stable shape across node types (x-extra, type, field filtering, tag
# slugging) and should be used to avoid drift. The ingest pipeline derives snippets
# and slugs IDs before delegating to these canonical functions.
logger = get_logger("ingest-normalize")
try:
    from shared.normalize import (
        normalize_timestamp as _shared_normalize_timestamp,
        normalize_event as _shared_normalize_event,
        normalize_decision as _shared_normalize_decision,
        normalize_transition as _shared_normalize_transition,
        normalize_alias_edge as _shared_normalize_alias_edge,
    )
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "shared.normalize not found. Make sure you run from the repo root so "
        "Python loads 'sitecustomize.py' (which adds packages/*/src to sys.path)."
    ) from exc

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")

def norm_timestamp(ts: str) -> str:
    # Delegate canonical timestamp shape to shared normaliser to avoid drift
    return _shared_normalize_timestamp(ts)

def norm_text(s: str | None, max_len: int | None = None) -> str | None:
    """Trim and normalise free‑text fields, optionally truncating to max_len."""
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", s).strip()
    s = re.sub(r"\s+", " ", s)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s

def normalize_decision(d: dict) -> dict:
    """
    Normalise a decision record.  Applies ingestion‑specific text trimming and
    ID slugging before delegating to the shared normaliser to enforce the
    canonical shape.  Tags are passed through unchanged here and will be
    slugged, deduplicated and order‑preserved by the shared normaliser.

    Parameters
    ----------
    d: dict
        The raw decision dictionary from upstream.

    Returns
    -------
    dict
        A canonicalised decision dictionary.
    """
    out: dict = {}
    raw_id = str(d.get("id", ""))
    out["id"] = raw_id if ID_RE.match(raw_id) else slugify_id(raw_id)
    out["option"] = norm_text(d.get("option"), 300)
    out["rationale"] = norm_text(d.get("rationale"), 600)
    out["timestamp"] = norm_timestamp(d["timestamp"])
    out["decision_maker"] = norm_text(d.get("decision_maker"), 120)
    # Pass raw tags through; the shared normaliser will slugify/deduplicate
    raw_tags = d.get("tags")
    if isinstance(raw_tags, list):
        out["tags"] = list(raw_tags)
    elif raw_tags is None:
        out["tags"] = []
    else:
        out["tags"] = [raw_tags]
    # Supported fields that must remain arrays of strings
    for k in ("supported_by", "based_on", "transitions"):
        arr = d.get(k) or []
        if not isinstance(arr, list):
            arr = []
        out[k] = [str(x) for x in arr]
    # Preserve x-extra from the raw document when it is a dict
    x_extra = d.get("x-extra")
    if isinstance(x_extra, dict):
        out["x-extra"] = x_extra
    # Delegate to shared normaliser for final canonical shape
    return _shared_normalize_decision(out)

def normalize_event(e: dict) -> dict:
    """
    Normalise an event record.  Performs ingestion‑specific text trimming,
    snippet synthesis and ID slugging before delegating to the shared
    normaliser.  Tags are passed through raw and will be slugged,
    deduplicated and order‑preserved by the shared normaliser.  Unknown
    fields are dropped by the shared normaliser.
    """
    out: dict = {}
    raw_id = str(e.get("id", ""))
    out["id"] = raw_id if ID_RE.match(raw_id) else slugify_id(raw_id)
    out["timestamp"] = norm_timestamp(e["timestamp"])
    out["summary"] = norm_text(e.get("summary"), 120)
    out["description"] = norm_text(e.get("description"))
    # Repair summary if missing/empty or equals the ID
    if not out.get("summary") or out["summary"] == out["id"]:
        desc = out.get("description") or ""
        out["summary"] = norm_text(desc[:96]) or "(no-summary)"
        log_stage(logger, "ingest", "summary_repaired",
                  node_type="event", node_id=out["id"])
    # Derive or trim snippet.  If a snippet is provided use it; otherwise
    # synthesise from the first sentence of the description up to 160 chars.
    snippet = e.get("snippet")
    if snippet:
        out["snippet"] = norm_text(snippet, 160)
    else:
        desc = out.get("description") or ""
        first = desc.split(".")[0][:160]
        out["snippet"] = norm_text(first, 160)
        log_stage(logger, "ingest", "snippet_synthesized",
                  node_type="event", node_id=out["id"])
    # Pass raw tags; the shared normaliser will slugify/deduplicate
    raw_tags = e.get("tags")
    if isinstance(raw_tags, list):
        out["tags"] = list(raw_tags)
    elif raw_tags is None:
        out["tags"] = []
    else:
        out["tags"] = [raw_tags]
    arr = e.get("led_to") or []
    out["led_to"] = [str(x) for x in arr] if isinstance(arr, list) else []
    # Preserve x-extra from the raw event when it is a dict
    x_extra = e.get("x-extra")
    if isinstance(x_extra, dict):
        out["x-extra"] = x_extra
    # Delegate to shared normaliser for final canonical shape
    return _shared_normalize_event(out)

def normalize_transition(t: dict) -> dict:
    """
    Normalise a transition record.  Performs ingestion‑specific text trimming
    and ID slugging before delegating to the shared normaliser.  Tags are
    passed through raw and will be slugged, deduplicated and order‑preserved
    by the shared normaliser.
    """
    out: dict = {}
    raw_id = str(t.get("id", ""))
    out["id"] = raw_id if ID_RE.match(raw_id) else slugify_id(raw_id)
    out["from"] = str(t.get("from"))
    out["to"] = str(t.get("to"))
    out["relation"] = t.get("relation") or "causal"
    out["reason"] = norm_text(t.get("reason"), 280)
    out["timestamp"] = norm_timestamp(t["timestamp"])
    raw_tags = t.get("tags")
    if isinstance(raw_tags, list):
        out["tags"] = list(raw_tags)
    elif raw_tags is None:
        out["tags"] = []
    else:
        out["tags"] = [raw_tags]
    # Preserve x-extra from the raw transition when it is a dict
    x_extra = t.get("x-extra")
    if isinstance(x_extra, dict):
        out["x-extra"] = x_extra
    # Delegate to shared normaliser
    return _shared_normalize_transition(out)

def normalize_alias_edge(d: dict) -> dict:
    """Normalise an alias overlay edge for ingestion."""
    # No ID slugging – fixture authors control alias edge IDs
    return _shared_normalize_alias_edge(dict(d))

def normalize_tags(tags: list[str]) -> list[str]:
    """
    Canonical tag normalisation for ingestion.  Tags are lower‑cased,
    normalised using underscores and deduplicated while preserving their
    original order.  This helper mirrors the behaviour of the shared
    normaliser and should be used sparingly since modern code delegates
    tag normalisation to the shared functions.  No sorting is applied.
    """
    normalised: list[str] = []
    seen: set[str] = set()
    for t in tags or []:
        try:
            slug = slugify_tag(str(t))
        except Exception:
            slug = slugify_tag(f"{t}")
        if slug and slug not in seen:
            normalised.append(slug)
            seen.add(slug)
    return normalised

def derive_backlinks(decisions: dict, events: dict, transitions: dict) -> None:
    """Shim: delegates to link_utils.derive_links for reciprocity."""
    return derive_links(decisions, events, transitions)
