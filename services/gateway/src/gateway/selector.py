from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Set

from core_utils import jsonx
from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence
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
        # Use canonical JSON; compute byte size deterministically.
        payload = ev.model_dump(mode="python", exclude_none=True)
        s = jsonx.dumps({"ev": payload})
        size = len(s.encode("utf-8"))
        logger.info("selector.bundle_size_bytes", extra={"bytes": size})
        return size
    except Exception as e:
        logger.warning("selector.bundle_size_bytes.error", extra={"error": str(e)})
        return 0

def evidence_prompt_tokens(ev: WhyDecisionEvidence) -> int:
    """Estimate tokens for the serialized evidence (proxy for prompt weight)."""
    # Canonical serializer → stable token estimates across services.
    txt = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True))
    tokens = estimate_text_tokens(txt)
    logger.info("selector.evidence_prompt_tokens", extra={"tokens": tokens})
    return tokens

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
        ranked = sorted(
            list(events),
            key=lambda e: (-_sim_text(e), _ts_iso(e), e.get("id") or ""),
        )
        logger.info(
            "selector.rank_events",
            extra={
                "count": len(ranked),
                "anchor_id": getattr(_anchor, "id", None),
                "policy": "sim_desc__ts_iso_asc__id_asc",
            },
        )
        return ranked
    except Exception:
        # Fallback: timestamp desc → id asc
        ranked = sorted(
            list(events),
            key=lambda e: (e.get("timestamp") or "", e.get("id") or ""),
            reverse=True,
        )
        logger.warning(
            "selector.rank_events.fallback",
            extra={"count": len(ranked), "policy": "ts_desc__id_asc"},
        )
        return ranked

