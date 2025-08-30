from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Set, Tuple

import orjson

from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence

from core_validator import canonical_allowed_ids

import time
from core_logging import get_logger
from shared.tokens import estimate_text_tokens

logger = get_logger("gateway.selector")

def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
    """
    Back-compat helper for legacy metrics in evidence.py.
    Returns the serialized size (bytes) of the evidence object.
    Note: This does NOT drive any pruning/gating logic (tokens do).
    """
    try:
        return len(orjson.dumps(ev.model_dump(mode="python")))
    except Exception:
        return len(str(ev).encode("utf-8"))

def evidence_prompt_tokens(ev: WhyDecisionEvidence) -> int:
    """Estimate tokens for the serialized evidence (proxy for prompt weight)."""
    return estimate_text_tokens(orjson.dumps(ev.model_dump(mode="python")).decode())

# ------------------------------------------------------------------ #
#  helpers                                                           #
# ------------------------------------------------------------------ #

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
    """Simple Jaccard similarity between two short texts."""
    ta, tb = _text_tokens(a), _text_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def _score(item: Dict[str, Any], anchor: WhyDecisionAnchor) -> Tuple[float, float]:
    """
    Score by recency (newer first) and similarity (higher first).
    Returning negatives lets us sort ascending.
    """
    ts_dt = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    recency_score = -ts_dt.timestamp()
    sim_score     = -_sim(
        item.get("summary") or item.get("description", ""), 
        anchor.rationale
    )
    return (recency_score, sim_score)

def rank_events(anchor: WhyDecisionAnchor, events: list[dict]) -> list[dict]:
    """Deterministically rank events for Answer/Response policies.

    Order: similarity desc → timestamp asc (ISO) → id asc.
    """
    if not events:
        return []
    try:
        _anchor = anchor or WhyDecisionAnchor(id="unknown")
        def _sim_text(ev: dict) -> float:
            txt = (ev.get("summary") or ev.get("description") or "").strip()
            return float(_sim(txt, _anchor.rationale or ""))
        def _ts_iso(ev: dict) -> str:
            return ev.get("timestamp") or ""
        return sorted(
            list(events),
            key=lambda e: (-_sim_text(e), _ts_iso(e), e.get("id") or ""),
        )
    except Exception:
        # Fallback: timestamp desc → id asc
        return sorted(list(events), key=lambda e: (e.get("timestamp") or "", e.get("id") or ""), reverse=True)

