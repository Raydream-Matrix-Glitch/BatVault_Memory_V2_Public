"""
Intent router for the Gateway service (Milestone-4).

When the client supplies a ``functions`` array, this module decides which
Memory-API helper endpoints to call and returns metadata needed for
structured logging.

Current routing map
-------------------
* ``search_similar``      →  POST  {MEMORY_API_BASE}/api/resolve/text
* ``get_graph_neighbors`` →  POST  {MEMORY_API_BASE}/api/graph/expand_candidates
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx
from core_observability.otel import inject_trace_context


async def route_query(
    question: str,
    functions: List[Any] | None = None,
) -> Dict[str, Any]:
    """
    Invoke Memory-API helpers requested via *functions* and return routing
    metadata.

    Parameters
    ----------
    question:
        The user’s natural-language question.
    functions:
        A list containing either plain strings (``"search_similar"``) or
        function-manifest dictionaries with a ``"name"`` field.

    Returns
    -------
    dict
        A dictionary with the keys required by the structured-logging
        contract: ``function_calls``, ``routing_confidence``,
        ``routing_model_id``.
    """
    # Normalise input to a list of names
    names: List[str] = []
    for f in functions or []:
        if isinstance(f, str):
            names.append(f)
        elif isinstance(f, dict):
            name = f.get("name")
            if name:
                names.append(name)

    from core_config import get_settings
    settings = get_settings()
    base = os.getenv("MEMORY_API_BASE", settings.memory_api_url)

    results: Dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        # search_similar → Memory-API text resolver
        if "search_similar" in names:
            try:
                # POST the query under the canonical `q` key per Memory‑API contract
                # propagate trace context on every outbound call so the Memory‑API
                # spans are children of this span; without headers the trace would split
                resp = await client.post(
                    f"{base}/api/resolve/text",
                    json={"q": question},
                    headers=inject_trace_context({}),
                )
                if resp.status_code == 200:
                    results["search_similar"] = resp.json()
            except Exception:
                # Memory‑API search unavailable or timed out; skip this helper
                pass
            except httpx.HTTPError:
                # timeout or connection error; skip this helper
                results["search_similar"] = None

        # get_graph_neighbors → Memory-API graph expand
        if "get_graph_neighbors" in names:
            # Default: raw question as node_id, override if args provided
            payload: Dict[str, Any] = {"node_id": question}
            for f in functions or []:
                if isinstance(f, dict) and f.get("name") == "get_graph_neighbors":
                    args = f.get("arguments") or {}
                    if "node_id" in args:
                        payload["node_id"] = args["node_id"]
                    break
            try:
                resp = await client.post(
                    f"{base}/api/graph/expand_candidates",
                    json=payload,
                    headers=inject_trace_context({}),
                )
                if resp.status_code == 200:
                    results["get_graph_neighbors"] = resp.json()
            except httpx.HTTPError:
                # timeout or connection error; skip this helper
                results["get_graph_neighbors"] = None

    return {
        "function_calls":      names,
        "routing_confidence":  1.0 if names else 0.0,
        "routing_model_id":    "router_stub_v1",
        "results":             results,              # ← NEW
    }