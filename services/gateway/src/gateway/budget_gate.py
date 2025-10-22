import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from core_utils.fingerprints import sha256_hex, ensure_sha256_prefix, canonical_json
from core_logging import get_logger, log_stage
from core_utils.graph import derive_events_from_edges as _derive_events

logger = get_logger("gateway.budget")

@dataclass(frozen=True)
class GatePolicy:
    edge_allowlist: Tuple[str, ...] = ("LED_TO", "CAUSAL")
    max_edges: int = 256
    max_events: int = 8
    max_cited_ids: int = 8

def _trim_edges(edges: List[Dict[str, Any]], allow: Tuple[str, ...], limit: int) -> List[Dict[str, Any]]:
    filtered = [e for e in (edges or []) if (e or {}).get("type") in allow]
    return filtered[: max(0, limit)]

def _pick_top_events(edges: List[Dict[str, Any]], limit: int) -> List[str]:
    # Deterministic, schema-safe: rank by (timestamp desc, id asc). Two stable sorts.
    evs = _derive_events({"edges": edges}) or []
    evs_by_id = sorted(evs, key=lambda ev: (ev.get("id") or ""))
    evs_sorted = sorted(evs_by_id, key=lambda ev: (ev.get("timestamp") or ""), reverse=True)
    return [ev["id"] for ev in evs_sorted[: max(0, limit)]]

def run_gate(
    envelope: Dict[str, Any],
    evidence_obj: Any,
    *,
    request_id: str,
) -> tuple[Dict[str, Any], Any]:
    """
    Deterministic, LLM-free budget gate:
      - filter edges by allowlist
      - clamp counts (edges/events/cited_ids)
      - emit a small plan with stable fingerprints (no tokens/messages)
    """
    # Resolve policy from env with sensible defaults
    policy = GatePolicy(
        edge_allowlist=tuple(
            ((envelope.get("policy") or {}).get("edge_allowlist")
             or os.getenv("BUDGET_EDGE_ALLOWLIST")
             or os.getenv("GATE_EDGE_ALLOWLIST")
             or "LED_TO,CAUSAL").split(",")
        ),
        max_edges=int((envelope.get("policy") or {}).get("max_edges")
                      or int(os.getenv("BUDGET_MAX_EDGES", os.getenv("GATE_MAX_EDGES", "256")))),
        max_events=int((envelope.get("policy") or {}).get("max_events")
                       or int(os.getenv("BUDGET_MAX_EVENTS", os.getenv("GATE_MAX_EVENTS", "8")))),
        max_cited_ids=int((envelope.get("policy") or {}).get("max_cited_ids")
                          or int(os.getenv("BUDGET_MAX_CITED_IDS", os.getenv("GATE_MAX_CITED_IDS", "8")))),
    )

    # JSON-first: treat evidence as mapping/object without casting
    ev = evidence_obj

    # Deterministic extraction of edges from dict-like or attribute-style evidence
    edges_in: List[Dict[str, Any]] = []
    graph = None
    if isinstance(ev, dict):
        graph = ev.get("graph")
    else:
        graph = getattr(ev, "graph", None)
    if isinstance(graph, dict):
        edges_in = list(graph.get("edges") or [])
    else:
        edges_attr = getattr(graph, "edges", None)
        if edges_attr is not None:
            edges_in = list(edges_attr or [])
    edges_out = _trim_edges(edges_in, policy.edge_allowlist, policy.max_edges)

    # Build trimmed evidence (replace edges only) as a plain mapping
    if isinstance(ev, dict):
        trimmed: Any = {**ev, "graph": {**(ev.get("graph") or {}), "edges": edges_out}}
    elif hasattr(ev, "model_dump"):
        base = ev.model_dump(mode="python", by_alias=True)  # type: ignore[attr-defined]
        trimmed = {**base, "graph": {**(base.get("graph") or {}), "edges": edges_out}}
    else:
        trimmed = {"graph": {"edges": edges_out}}
    # Deterministic top events & cited IDs
    top_events = _pick_top_events(edges_out, policy.max_events)
    cited_ids = [getattr(getattr(ev, "anchor", None) or {}, "id", None)] + top_events
    cited_ids = [i for i in cited_ids if i][: policy.max_cited_ids]

    # Fingerprint the effective policy
    cfg = {
        "edge_allowlist": policy.edge_allowlist,
        "max_edges": policy.max_edges,
        "max_events": policy.max_events,
        "max_cited_ids": policy.max_cited_ids,
    }
    budget_cfg_fp = ensure_sha256_prefix(sha256_hex(canonical_json(cfg)))

    fields = {
        # LLM-free: zero token counts; empty messages; no shrinks
        "prompt_tokens": 0,
        "overhead_tokens": 0,
        "evidence_tokens": 0,
        "desired_completion_tokens": 0,
        "max_tokens": 0,
        "messages": [],
        "shrinks": [],
        "fingerprints": {"budget_cfg_fp": budget_cfg_fp, "prompt": "none"},
        "logs": [],
    }

    # Attach helper keys for templater/builder (JSON-first, deterministic)
    if isinstance(trimmed, dict):
        trimmed["_events_ranked_top"] = top_events
        trimmed["_cited_ids_gate"] = cited_ids
        trimmed["_budget_cfg_fp"] = budget_cfg_fp
    # Strategic audit log
    log_stage(
        logger, "gate", "applied",
        request_id=request_id,
        edges_in=len(edges_in), edges_out=len(edges_out),
        events=len(top_events), cited=len(cited_ids), budget_cfg_fp=budget_cfg_fp)
    return fields, trimmed
