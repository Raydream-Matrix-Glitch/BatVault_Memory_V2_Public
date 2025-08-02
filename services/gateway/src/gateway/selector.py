from __future__ import annotations
import datetime as dt
import hashlib
import math
from typing import Any, Dict, List, Set, Tuple, Sequence
import core_metrics

import orjson

from core_models.models import (
     WhyDecisionResponse,
     WhyDecisionAnswer,
     WhyDecisionAnchor,
)
import time
from core_logging import log_stage, get_logger
import core_metrics
from core_config.constants import (
    MAX_PROMPT_BYTES,
    SELECTOR_TRUNCATION_THRESHOLD,
    MIN_EVIDENCE_ITEMS,
    SELECTOR_MODEL_ID,
)

logger = get_logger("selector")


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
    """Return (possibly) truncated evidence and the selector_meta block."""
    # 1) If under the soft threshold, emit no-truncate meta and exit early.
    raw_size = bundle_size_bytes(ev)
    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
        ev.allowed_ids = sorted(x.get("id") for x in ev.events if x.get("id"))
        meta = {
            "selector_truncation": False,
            "total_neighbors_found": len(ev.events),
            "final_evidence_count": len(ev.events),
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
                core_metrics.counter("dropped_evidence_id_total", 1, id=_id)
        except Exception:
            pass
        return ev, meta

    # 2) Full sort and prune loop: keep dropping worst items until under hard limit
    start = time.perf_counter()
    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor))
    ev.events = events_sorted.copy()
    while bundle_size_bytes(ev) > MAX_PROMPT_BYTES and len(ev.events) > MIN_EVIDENCE_ITEMS:
        ev.events.pop()
    elapsed_ms = (time.perf_counter() - start) * 1000
    # emit selector latency histogram (for p95 ≤2ms target)
    core_metrics.histogram("selector_ms", elapsed_ms)

    # 3) Build metadata
    final_size = bundle_size_bytes(ev)
    kept_ids   = {x.get("id") for x in ev.events if x.get("id")}
    dropped    = [x.get("id") for x in events_sorted if x.get("id") and x.get("id") not in kept_ids]
    ev.allowed_ids = sorted(kept_ids)

    meta = {
        "selector_truncation": len(dropped) > 0,
        "total_neighbors_found": len(events_sorted),
        "final_evidence_count": len(ev.events),
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
            core_metrics.counter("dropped_evidence_id_total", 1, id=_id)
    except Exception:
        pass

    return ev, meta
