from __future__ import annotations
import time
import datetime as dt
from typing import Any, Dict, Set, List, Tuple
from core_utils import jsonx
from core_models_gen import WhyDecisionAnchor, WhyDecisionEvidence
from core_logging import get_logger
from core_logging import log_stage, log_once, current_request_id
from core_utils.tokens import estimate_text_tokens
from core_metrics import histogram as metric_histogram

logger = get_logger("gateway.selector")
logger.propagate = False

# Public, stable policy identifier used in audits & meta.selection_metrics.ranking_policy.
# Order semantics: similarity DESC → timestamp DESC (ISO) → id ASC
SELECTOR_POLICY_ID = "sim_desc__ts_iso_desc__id_asc"
__all__ = [
    "bundle_size_bytes", "evidence_prompt_tokens",
    "rank_events", "compute_scores", "run_selector",
    "SELECTOR_POLICY_ID",
]

def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
    """
    Back-compat helper for legacy metrics in evidence.py.
    Returns the serialized size (bytes) of the evidence object.
    Note: This does NOT drive any pruning/gating logic (tokens do).
    """
    # Use canonical JSON; compute byte size deterministically.
    payload = ev.model_dump(mode="python", exclude_none=True)
    s = jsonx.dumps({"ev": payload})
    size = len(s.encode("utf-8"))
    log_stage(
        logger, "selector", "selector.bundle_size_bytes",
        bytes=size, request_id=(current_request_id() or "unknown")
    )
    return size

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
    except (ValueError, AttributeError):
        return None


def _text_tokens(s: str | None) -> Set[str]:
    return set((s or "").lower().split())

def _jaccard(ta: Set[str], tb: Set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def rank_events(anchor: WhyDecisionAnchor, events: list[dict]) -> list[dict]:
    """Deterministically rank events for Answer/Response policies.

    Order: similarity DESC → timestamp DESC (ISO) → id ASC.
    """
    _t0 = time.perf_counter()
    if not events:
        metric_histogram("gateway_stage_selector_seconds", time.perf_counter() - _t0, policy_id=SELECTOR_POLICY_ID)
        return []
    try:
        # Precompute sort keys once per request for a cheaper hot path.
        _anchor = anchor or WhyDecisionAnchor(id="unknown")
        # Build the anchor token set from the normative `description` field, falling back
        # to `title`.  Do not use the optional `rationale` field to avoid relying on
        # non‑schema attributes.  Missing values produce an empty token set.
        anchor_tokens: Set[str] = _text_tokens(
            getattr(_anchor, "description", None) or getattr(_anchor, "title", None) or ""
        )

        # Per-request memo of node→timestamp used in sorts (id → iso str)
        ts_by_id: Dict[str, str] = {}
        # Prepared tuples: (id, sim, ts_iso, ev)
        prepared: List[Tuple[str, float, str, dict]] = []

        for ev in events:
            if not isinstance(ev, dict):
                try:
                    ev = ev.model_dump(mode="python")  # type: ignore[attr-defined]
                except AttributeError:
                    try:
                        ev = dict(ev)
                    except (TypeError, ValueError):
                        continue  # Skip unrecognised entries defensively
            _id = (ev.get("id") or "")
            text = (ev.get("summary") or ev.get("description") or "").strip()
            sim = float(_jaccard(_text_tokens(text), anchor_tokens))
            ts_iso = (ev.get("timestamp") or "")
            ts_by_id[_id] = ts_iso
            prepared.append((_id, sim, ts_iso, ev))

        # Stable multi-pass sort on precomputed keys:
        #   1) id ASC
        #   2) timestamp DESC
        #   3) similarity DESC
        prepared.sort(key=lambda t: t[0])                  # id ASC
        prepared.sort(key=lambda t: t[2], reverse=True)    # ts DESC
        prepared.sort(key=lambda t: t[1], reverse=True)    # sim DESC
        ranked = [t[3] for t in prepared]
        log_once(
            logger,
            key=f"selector.rank_events:{getattr(_anchor, 'id', None)}",
            event="selector.rank_events",
            stage="selector",
            count=len(ranked),
            anchor_id=getattr(_anchor, "id", None),
            policy=SELECTOR_POLICY_ID,
            ts_cache_size=len(ts_by_id),
            request_id=(current_request_id() or "unknown"),
        )
        metric_histogram("gateway_stage_selector_seconds", time.perf_counter() - _t0, policy_id=SELECTOR_POLICY_ID)
        return ranked
    except (TypeError, ValueError, AttributeError):
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
        metric_histogram("gateway_stage_selector_seconds", time.perf_counter() - _t0, policy_id=SELECTOR_POLICY_ID)
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
        # Precompute anchor tokens once; reuse for all items.  Use the normative
        # `description` field, falling back to `title`.  Avoid using optional
        # `rationale` to remain schema‑aligned.
        anchor_tokens: Set[str] = _text_tokens(
            getattr(_anchor, "description", None) or getattr(_anchor, "title", None) or ""
        )
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
                except AttributeError:
                    it = dict(it)  # best-effort
            _id = it.get("id")
            if not _id:
                continue
            text = (it.get("summary") or it.get("description") or "").strip()
            sim = float(_jaccard(_text_tokens(text), anchor_tokens))
            imp = float(it.get("importance") or 0.0)
            # Recency in days (absolute), default 0 when missing timestamps
            r_days: float = 0.0
            try:
                ts = it.get("timestamp") or None
                if ts and a_dt is not None:
                    i_dt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    r_days = abs((a_dt - i_dt).days)
            except (ValueError, TypeError, AttributeError):
                r_days = 0.0
            scores[_id] = {"sim": sim, "recency_days": float(r_days), "importance": imp}
        # Strategic: tiny sample for the audit drawer without flooding logs
        sample = [{"id": k, **v} for k, v in list(scores.items())[:3]]
        log_stage(
            logger, "selector", "selector.metrics.scores",
            anchor_id=getattr(_anchor, "id", None), sample=sample, count=len(scores),
            request_id=(current_request_id() or "unknown")
        )
    except (TypeError, ValueError, AttributeError):
        # Defensive: never fail the request because of metrics
        logger.warning("selector.metrics.error", extra={"reason": "compute_scores_failed"})
    return scores

# ------------------------------------------------------------------ #
#  primary entry for builder: ranks + scores + policy id             #
# ------------------------------------------------------------------ #
def run_selector(anchor: WhyDecisionAnchor, items: List[Any]) -> Tuple[List[dict], Dict[str, Dict[str, float]], str]:
    # Strategic structured logging for audit and replay
    log_stage(logger, "selector", "start", anchor_id=getattr(anchor, "id", None), item_count=len(items or []))
    """
    Rank candidate items and compute per-id scores.
    Returns: (ranked_items, scores_by_id, SELECTOR_POLICY_ID).
    Notes:
      - The Budget Gate must only trim by token count; it MUST NOT re-rank.
      - Items may be dicts or Pydantic models; we normalise to dicts.
    """
    # Normalise inputs (tolerate Pydantic models)
    normalised: List[dict] = []
    for it in (items or []):
        if isinstance(it, dict):
            normalised.append(it)
            continue
        try:
            normalised.append(it.model_dump(mode="python"))  # type: ignore[attr-defined]
        except Exception:
            try:
                normalised.append(dict(it))
            except Exception:
                # Skip unrecognised entries defensively
                continue
    ranked = rank_events(anchor, normalised)
    scores = compute_scores(anchor, ranked)
    log_stage(logger, "selector", "ranked", anchor_id=getattr(anchor, "id", None), ranked_count=len(ranked), policy=SELECTOR_POLICY_ID)
    # Strategic sample for audit drawer without flooding logs
    sample_ids = [ev.get("id") for ev in ranked[:3]]
    log_stage(logger, "selector", "selector.run_selector",
              anchor_id=getattr(anchor, "id", None),
              policy=SELECTOR_POLICY_ID,
              ranked_count=len(ranked),
              sample=sample_ids)
    return ranked, scores, SELECTOR_POLICY_ID

