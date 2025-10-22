from __future__ import annotations
from typing import Iterable, List, Dict, Tuple
from core_utils.domain import storage_key_to_anchor, is_valid_anchor
from core_models.ontology import canonical_edge_type
from core_logging import get_logger, log_stage, current_request_id

__all__ = ["to_wire_edges"]

logger = get_logger("core_models.graph_view")

def to_wire_edges(storage_edges: Iterable[dict] | None) -> List[dict]:
    """Shape storage-edge dicts into *wire* edges (anchors only), deduplicated.

    - Converts storage keys in 'from'/'to' to wire anchors '<domain>#<id>'.
    - Canonicalizes edge 'type' token.
    - Deduplicates by (type, from, to, timestamp).
    - Sorts deterministically by the same tuple.
    - Drops non-wire fields (e.g. 'domain').

    Pure, deterministic, CPU-fast. No I/O. Tolerates malformed items by skipping them.
    """
    out: List[dict] = []
    seen: set[Tuple[str, str, str, str]] = set()
    for e in (storage_edges or []):
        if not isinstance(e, dict):
            continue
        try:
            et = canonical_edge_type(e.get("type"))
            fr = storage_key_to_anchor(e.get("from"))
            to = storage_key_to_anchor(e.get("to"))
            ts = str(e.get("timestamp") or "")
            if not (is_valid_anchor(fr) and is_valid_anchor(to) and ts):
                log_stage(
                    logger, "graph_view", "skip_invalid_edge",
                    reason="invalid_anchor_or_ts",
                    request_id=(current_request_id() or "unknown"),
                )
                continue
            k = (et, fr, to, ts)
            if k in seen:
                continue
            seen.add(k)
            out.append({"type": et, "from": fr, "to": to, "timestamp": ts})
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # Skip malformed entries (fail-closed); schema validation enforces shape later.
            log_stage(
                logger, "graph_view", "skip_malformed_edge",
                error=type(exc).__name__, request_id=(current_request_id() or "unknown"),
            )
            continue
    out.sort(key=lambda x: (x["type"], x["from"], x["to"], x["timestamp"]))
    return out