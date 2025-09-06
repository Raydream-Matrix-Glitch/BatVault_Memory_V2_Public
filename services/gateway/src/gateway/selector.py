from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Set

from core_utils import jsonx
from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence
from core_logging import get_logger
from shared.tokens import estimate_text_tokens

logger = get_logger("gateway.selector")
logger.propagate = False

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

    Order: similarity DESC → timestamp DESC (ISO) → id ASC.
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
        # Stable multi-pass sort:
        #   1) id ASC
        #   2) timestamp DESC
        #   3) similarity DESC
        ranked = list(events)
        ranked.sort(key=lambda e: (e.get("id") or ""))                       # id ASC
        ranked.sort(key=lambda e: (_ts_iso(e) or ""), reverse=True)          # ts DESC
        ranked.sort(key=lambda e: (_sim_text(e)), reverse=True)              # sim DESC
        logger.info(
            "selector.rank_events",
            extra={
                "count": len(ranked),
                "anchor_id": getattr(_anchor, "id", None),
                "policy": "sim_desc__ts_iso_desc__id_asc",
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
    
def compute_scores(anchor: WhyDecisionAnchor, items: list[dict]) -> Dict[str, Dict[str, float]]:
    """Compute per-ID confidence signals used for explainability.

    Emits a mapping: {id: {sim, recency_days, importance}}.
    - sim:       Jaccard similarity between item text and anchor rationale (0..1).
    - recency_days: Absolute days between anchor.timestamp and item.timestamp. 0 if unknown.
    - importance:   Fixture prior from the item (0..1), default 0.0.
    """
    scores: Dict[str, Dict[str, float]] = {}
    if not items:
        return scores
    try:
        _anchor = anchor or WhyDecisionAnchor(id="unknown")
        # Parse anchor timestamp once
        try:
            a_ts = _anchor.timestamp or None
            a_dt = dt.datetime.fromisoformat(a_ts.replace("Z", "+00:00")) if a_ts else None
        except Exception:
            a_dt = None

        for it in items:
            if not isinstance(it, dict):
                try:
                    it = it.model_dump(mode="python")  # type: ignore[attr-defined]
                except Exception:
                    it = dict(it)  # best-effort
            _id = it.get("id")
            if not _id:
                continue
            text = (it.get("summary") or it.get("description") or "").strip()
            sim = float(_sim(text, getattr(_anchor, "rationale", None) or ""))  # type: ignore[name-defined]
            imp = float(it.get("importance") or 0.0)
            # Recency in days (absolute), default 0 when missing timestamps
            r_days: float = 0.0
            try:
                ts = it.get("timestamp") or None
                if ts and a_dt is not None:
                    i_dt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    r_days = abs((a_dt - i_dt).days)
            except Exception:
                r_days = 0.0
            scores[_id] = {"sim": sim, "recency_days": float(r_days), "importance": imp}
        # Strategic: log a tiny sample for the audit drawer without flooding logs
        try:
            sample = [{"id": k, **v} for k, v in list(scores.items())[:3]]
            logger.info("selector.metrics.scores", extra={"anchor_id": getattr(_anchor, "id", None), "sample": sample, "count": len(scores)})
        except Exception:
            pass
    except Exception:
        # Defensive: never fail the request because of metrics
        logger.warning("selector.metrics.error", extra={"reason": "compute_scores_failed"})
    return scores

