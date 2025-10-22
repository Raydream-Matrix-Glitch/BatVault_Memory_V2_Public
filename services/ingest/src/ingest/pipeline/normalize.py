from __future__ import annotations
from typing import List, Dict, Tuple
from core_logging import get_logger, log_stage, current_request_id
from core_models.normalize import normalize_batch as _normalize_batch

logger = get_logger("ingest.normalize")

def normalize_once(nodes: List[dict] | None, edges: List[dict] | None) -> Tuple[list[dict], list[dict]]:
    """
    Canonical batch normalizer (single source of truth).
    - Delegates to core_models.normalize.normalize_batch (pure, deterministic).
    - Fail fast on any normalization error.
    - No silent coercions; rejects non-RFC3339Z timestamps.
    """
    nodes = nodes or []
    edges = edges or []
    try:
        norm_nodes, norm_edges = _normalize_batch(nodes, edges)
        log_stage(
            logger, "normalize", "normalized",
            node_count=len(norm_nodes), edge_count=len(norm_edges),
            request_id=(current_request_id() or "unknown"),
        )
        return norm_nodes, norm_edges
    except (ValueError, TypeError) as e:
        log_stage(
            logger, "normalize", "failed",
            error=str(e), node_count=len(nodes), edge_count=len(edges),
            request_id=(current_request_id() or "unknown"),
        )
        raise