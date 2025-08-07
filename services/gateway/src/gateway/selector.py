from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Set, Tuple
import core_metrics

import orjson

from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence

# Expose shortcuts in the global namespace so Milestone-3 test-suites that
# still import these symbols *bare* keep working until they migrate.
import builtins as _b
_b.WhyDecisionEvidence = WhyDecisionEvidence
_b.WhyDecisionAnchor = WhyDecisionAnchor

import time
from core_logging import log_stage, get_logger
from core_config.constants import (
    MAX_PROMPT_BYTES,
    SELECTOR_TRUNCATION_THRESHOLD,
    MIN_EVIDENCE_ITEMS,
    SELECTOR_MODEL_ID,
)

logger = get_logger("selector")


def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
    return len(orjson.dumps(ev.model_dump(mode="python")))

# ------------------------------------------------------------------ #
#  helpers                                                           #
# ------------------------------------------------------------------ #
def _union_ids(ev: WhyDecisionEvidence) -> list[str]:
    """Return anchor ∪ events ∪ transition IDs (spec §B2)."""
    ids = {ev.anchor.id}
    ids.update([e.get("id") for e in ev.events if isinstance(e, dict)])
    ids.update([t.get("id") for t in ev.transitions.preceding +
                               ev.transitions.succeeding
                if isinstance(t, dict)])
    return sorted([x for x in ids if x])


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


def truncate_evidence(ev: WhyDecisionEvidence) -> Tuple[WhyDecisionEvidence, Dict[str, Any]]:

    # ── Work on a deep copy so the caller keeps the original ──────────────
    ev = ev.model_copy(deep=True)
    _pre_trunc_ids: set[str] = set(_union_ids(ev))          # snapshot

    # 1) If under the soft threshold, emit no-truncate meta and exit early.
    raw_size = bundle_size_bytes(ev)
    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
        ev.allowed_ids = _union_ids(ev)
        meta = {
            # under soft limit ⇒ nothing was dropped
            "selector_truncation": False,
            "total_neighbors_found": len(ev.events)
               + len(ev.transitions.preceding)
               + len(ev.transitions.succeeding),
            "final_evidence_count": len(ev.allowed_ids),
            "dropped_evidence_ids": [],
            "bundle_size_bytes": raw_size,
            "max_prompt_bytes": MAX_PROMPT_BYTES,
            "selector_model_id": SELECTOR_MODEL_ID,
        }
        log_stage(logger, "selector", "selector_complete", **meta)
        # ── Metrics ----------------------------------------------------- #
        try:
            core_metrics.histogram("total_neighbors_found", float(meta["total_neighbors_found"]))
            core_metrics.histogram("final_evidence_count", float(meta["final_evidence_count"]))
            core_metrics.histogram("bundle_size_bytes", float(meta["bundle_size_bytes"]))
            if meta["selector_truncation"]:
                core_metrics.counter("selector_truncation", 1)
            for _id in meta["dropped_evidence_ids"]:
                core_metrics.counter("dropped_evidence_ids", 1, id=_id)
        except Exception:
            pass
        return ev, meta

    # 2) Full sort and prune loop: keep dropping worst items until under hard limit
    start = time.perf_counter()
    # unified candidate list (events **and** transitions) for pruning
    candidates = (
        [("event", e) for e in ev.events]
        + [("preceding", t) for t in ev.transitions.preceding]
        + [("succeeding", t) for t in ev.transitions.succeeding]
    )
    candidates.sort(key=lambda kv: _score(kv[1], ev.anchor))

    def _drop(kind: str, item: dict) -> None:
        if kind == "event":
            ev.events.remove(item)
        elif kind == "preceding":
            ev.transitions.preceding.remove(item)
        else:
            ev.transitions.succeeding.remove(item)

    while (
        bundle_size_bytes(ev) > SELECTOR_TRUNCATION_THRESHOLD
        and (
            len(ev.events)
            + len(ev.transitions.preceding)
            + len(ev.transitions.succeeding)
        )
        > MIN_EVIDENCE_ITEMS
    ):
        _drop(*candidates.pop())
    elapsed_ms = (time.perf_counter() - start) * 1000
    # emit selector latency histogram (for p95 ≤2ms target)
    core_metrics.histogram("selector_ms", elapsed_ms)

    # ------------------------------------------------------------------ #
    # 2b) Hard guarantee: never exceed MAX_PROMPT_BYTES (§M4).           #
    #     First clip long prose fields, then – as an absolute last       #
    #     resort – keep pruning below MIN_EVIDENCE_ITEMS (anchor only).  #
    # ------------------------------------------------------------------ #

    def _clip_text(obj: dict | Any) -> None:
        """Trim very long strings to ≤128 chars with an ellipsis."""
        for fld in ("summary", "description", "snippet", "rationale", "reason"):
            if isinstance(obj, dict):
                if fld in obj and isinstance(obj[fld], str) and len(obj[fld]) > 128:
                    obj[fld] = obj[fld][:125] + "…"
            else:  # Pydantic model / attr object
                if hasattr(obj, fld):
                    val = getattr(obj, fld)
                    if isinstance(val, str) and len(val) > 128:
                        setattr(obj, fld, val[:125] + "…")

    if bundle_size_bytes(ev) > MAX_PROMPT_BYTES:
        _clip_text(ev.anchor)
        for _e in ev.events:
            _clip_text(_e)
        for _t in ev.transitions.preceding + ev.transitions.succeeding:
            _clip_text(_t)

    while bundle_size_bytes(ev) > MAX_PROMPT_BYTES and len(ev.events) > 0:
        _drop(*candidates.pop())

    # 3) Build metadata
    kept_ids: set[str] = set(_union_ids(ev))            # after *all* pruning
    dropped:  list[str] = sorted(_pre_trunc_ids - kept_ids)
    ev.allowed_ids = sorted(kept_ids)

    # neighbours considered *before* truncation (anchor excluded)
    neighbor_count = max(len(_pre_trunc_ids) - 1, 0)

    # final bundle size after pruning
    final_size = bundle_size_bytes(ev)

    meta = {
        "selector_truncation": len(dropped) > 0,
        # neighbours considered *before* any truncation
        "total_neighbors_found": neighbor_count,
        "final_evidence_count": len(ev.allowed_ids),
        "dropped_evidence_ids": dropped,
        "bundle_size_bytes": final_size,
        "max_prompt_bytes": MAX_PROMPT_BYTES,
        "selector_model_id": SELECTOR_MODEL_ID,
    }
    log_stage(logger, "selector", "selector_complete", **meta)
    
    # ── Metrics --------------------------------------------------------- #
    try:
        core_metrics.histogram("total_neighbors_found", float(meta["total_neighbors_found"]))
        core_metrics.histogram("final_evidence_count", float(meta["final_evidence_count"]))
        core_metrics.histogram("bundle_size_bytes", float(meta["bundle_size_bytes"]))
        if meta["selector_truncation"]:
            core_metrics.counter("selector_truncation", 1)
        for _id in meta["dropped_evidence_ids"]:
            core_metrics.counter("dropped_evidence_ids", 1, id=_id)
    except Exception:
        pass

    return ev, meta
