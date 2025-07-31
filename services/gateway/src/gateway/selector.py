from __future__ import annotations
import orjson, datetime as dt, hashlib, math
from typing import Dict, Any, List, Tuple, Set

import orjson, datetime as dt
from typing import Dict, Any, List, Tuple, Set

from .models import WhyDecisionEvidence, WhyDecisionAnchor
from core_config.constants import (
    MAX_PROMPT_BYTES,
    SELECTOR_TRUNCATION_THRESHOLD,
    MIN_EVIDENCE_ITEMS,
    SELECTOR_MODEL_ID,
)

MAX_PROMPT_BYTES = 8192
SELECTOR_TRUNCATION_THRESHOLD = 6144


def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
    return len(orjson.dumps(ev.model_dump(mode="python")))


def _parse_ts(item: Dict[str, Any]) -> dt.datetime | None:
    ts = item.get("timestamp")
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _text_tokens(s: str | None) -> Set[str]:
    return set((s or "").lower().split())

def _sim(a: str | None, b: str | None) -> float:
    """Jaccard similarity between two short texts (deterministic baseline)."""
    ta, tb = _text_tokens(a), _text_tokens(b)
    inter = ta & tb
    union = ta | tb
    sim = _sim(item.get("summary") or item.get("description"), anchor.rationale)
    return (int(ts.timestamp()), sim)

def _score(item: Dict[str, Any], anchor: WhyDecisionAnchor) -> Tuple[int, float]:
    # Recency + similarity (baseline)
    ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (int(ts.timestamp()), 0.0)


def truncate_evidence(ev: WhyDecisionEvidence) -> Tuple[WhyDecisionEvidence, Dict[str, Any]]:
    """Return (possibly) truncated evidence and selector_meta."""
    size = bundle_size_bytes(ev)
    if size <= SELECTOR_TRUNCATION_THRESHOLD:
        return ev, {
            "selector_truncation": False,
            "total_neighbors_found": len(ev.events)
            + len(ev.transitions.preceding)
            + len(ev.transitions.succeeding),
            "final_evidence_count": len(ev.events)
            + len(ev.transitions.preceding)
            + len(ev.transitions.succeeding),
            "dropped_evidence_ids": [],
            "bundle_size_bytes": size,
            "max_prompt_bytes": MAX_PROMPT_BYTES,
            "selector_model_id": SELECTOR_MODEL_ID,               # B-2
        }

    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor), reverse=True)
    kept: List[Dict[str, Any]] = []
    for e in events_sorted:
        kept.append(e)
        ev.events = kept
        if (
            bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD
            and len(kept) >= MIN_EVIDENCE_ITEMS            # B-3
        ):
            break

# -- MIN_EVIDENCE_ITEMS safety net (rare edge: every neighbour too big) --
    if not kept and events_sorted:
        kept.append(events_sorted[0])
        ev.events = kept

    ids = {ev.anchor.id}
    ids.update([e.get("id") for e in kept if isinstance(e, dict)])
    ids.update([t.get("id") for t in ev.transitions.preceding if t.get("id")])
    ids.update([t.get("id") for t in ev.transitions.succeeding if t.get("id")])
    ev.allowed_ids = sorted(i for i in ids if i)

    meta = {
        "selector_truncation": True,
        "total_neighbors_found": len(events_sorted),
        "final_evidence_count": len(kept)
        + len(ev.transitions.preceding)
        + len(ev.transitions.succeeding),
        "dropped_evidence_ids": [x.get("id") for x in events_sorted[len(kept) :] if x.get("id")],
        "bundle_size_bytes": bundle_size_bytes(ev),
        "max_prompt_bytes": MAX_PROMPT_BYTES,
        "selector_model_id": SELECTOR_MODEL_ID,
    }
    return ev, meta
