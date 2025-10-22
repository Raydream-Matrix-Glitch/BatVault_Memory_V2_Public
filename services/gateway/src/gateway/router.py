from __future__ import annotations
from typing import Any, Dict, List
from core_logging import get_logger, log_stage

logger = get_logger("gateway.router")

SUPPORTED_FUNCS = {"search_similar"}

async def route_query(question: str, functions: List[Any] | None = None, *, request_id: str | None = None) -> Dict[str, Any]:
    """Very small rule-based router.

    * Always proposes ``search_similar``; this is cheap and improves recall.
    * Neighbor/graph traversal calls are not supported in v3 (see BASELINE.md).
    * Returns structured logging fields expected by app.py.
    """
    # Per v3 wire-contract invariants, we never propose or allow a "neighbors" call.
    # See BASELINE.md §15 (Memory → Gateway invariants) and §16 (Deprecations & Removal).
    calls: List[str] = ["search_similar"]

    info: Dict[str, Any] = {
        "function_calls": calls,
        "routing_confidence": 0.6,
        "routing_model_id": "rules_v1",
    }
    log_stage(logger, "router", "route",
              question_len=len(question or ""), calls=calls, request_id=request_id)
    return info