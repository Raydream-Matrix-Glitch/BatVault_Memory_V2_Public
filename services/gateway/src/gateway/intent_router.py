"""
Intent router for the Gateway service (Milestone-4).

Maps natural-language queries to Memory API function calls and
returns lightweight metadata used for audit logs. The actual Memory
API calls are performed later by the builder.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core_logging import get_logger
from .logging_helpers import stage as log_stage

logger = get_logger("gateway.intent_router")

SUPPORTED_FUNCS = {"search_similar", "get_graph_neighbors"}

def _normalise_functions(functions: List[Any] | None) -> List[str]:
    out: List[str] = []
    for f in functions or []:
        if isinstance(f, dict):
            name = f.get("name")
            if name:
                out.append(str(name))
        else:
            out.append(str(f))
    return [f for f in out if f in SUPPORTED_FUNCS]

async def route_query(question: str, functions: List[Any] | None = None) -> Dict[str, Any]:
    """Very small rule-based router.

    * Always proposes ``search_similar``; this is cheap and improves recall.
    * If the caller explicitly allowed ``get_graph_neighbors``, include it.
    * Returns structured logging fields expected by app.py.
    """
    allowed = set(_normalise_functions(functions))
    calls: List[str] = ["search_similar"]
    if "get_graph_neighbors" in allowed:
        calls.append("get_graph_neighbors")

    info: Dict[str, Any] = {
        "function_calls": calls,
        "routing_confidence": 0.75 if len(calls) == 2 else 0.6,
        "routing_model_id": "rules_v1",
    }
    try:
        log_stage("intent_router", "route", question_len=len(question or ""), calls=calls)
    except Exception:
        pass
    return info