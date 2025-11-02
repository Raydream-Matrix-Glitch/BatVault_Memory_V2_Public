import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from collections.abc import Iterable
from core_utils.fingerprints import sha256_hex, ensure_sha256_prefix, canonical_json
from core_logging import get_logger, log_stage
from core_utils.graph import derive_events_from_edges as _derive_events
from core_models.ontology import CAUSAL_EDGE_TYPES, canonical_edge_type
from core_config.constants import TIMEOUT_ENRICH_MS
try:
    from .selector import SELECTOR_POLICY_ID as _SELECTOR_POLICY_ID  # type: ignore
except ImportError:
    _SELECTOR_POLICY_ID = "unknown"

logger = get_logger("gateway.budget")

@dataclass(frozen=True)
class GatePolicy:
    edge_allowlist: Tuple[str, ...] = CAUSAL_EDGE_TYPES
    max_edges: int = 256
    max_events: int = 8
    max_cited_ids: int = 8

def _pick_top_events(edges: List[Dict[str, Any]], limit: int) -> List[str]:
    """
    Deterministic ranking for event IDs derived from edges:
      - stable sort by id (asc), then stable sort by timestamp (desc)
    """
    events = _derive_events({"edges": edges}) or []
    events_by_id = sorted(events, key=lambda ev: (ev.get("id") or ""))
    events_sorted = sorted(events_by_id, key=lambda ev: (ev.get("timestamp") or ""), reverse=True)
    return [ev["id"] for ev in events_sorted[: max(0, limit)]]

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
      - emit a small, schema-shaped plan (gateway.plan.json)
    """
    # Resolve policy (env → envelope → defaults). No broad try/except.
    # Canonical default based on ontology constants to avoid string drift.
    _default_allowlist_csv = ",".join(CAUSAL_EDGE_TYPES)
    _raw_allow = (
        (envelope.get("policy") or {}).get("edge_allowlist")
        or os.getenv("BUDGET_EDGE_ALLOWLIST")
        or os.getenv("GATE_EDGE_ALLOWLIST")
        or _default_allowlist_csv
    )
    # Accept either CSV (env/headers) or an iterable of strings.
    if isinstance(_raw_allow, str):
        tokens = [t for t in _raw_allow.split(",") if t]
    elif isinstance(_raw_allow, Iterable):
        tokens = list(_raw_allow)
    else:
        tokens = list(CAUSAL_EDGE_TYPES)
    # Normalize & validate policy tokens early
    _edge_allowlist = tuple(canonical_edge_type(t) for t in tokens)
    policy = GatePolicy(
        edge_allowlist=_edge_allowlist,
        max_edges=int((envelope.get("policy") or {}).get("max_edges")
                      or int(os.getenv("BUDGET_MAX_EDGES", os.getenv("GATE_MAX_EDGES", "256")))),
        max_events=int((envelope.get("policy") or {}).get("max_events")
                       or int(os.getenv("BUDGET_MAX_EVENTS", os.getenv("GATE_MAX_EVENTS", "8")))),
        max_cited_ids=int((envelope.get("policy") or {}).get("max_cited_ids")
                          or int(os.getenv("BUDGET_MAX_CITED_IDS", os.getenv("GATE_MAX_CITED_IDS", "8")))),
    )

    # Treat evidence as a mapping without mutating original object
    ev = evidence_obj

    # Extract edges deterministically from dict-like or attribute-style evidence
    edges_in: List[Dict[str, Any]] = []
    graph = ev.get("graph") if isinstance(ev, dict) else getattr(ev, "graph", None)
    if isinstance(graph, dict):
        edges_in = list(graph.get("edges") or [])
    else:
        edges_attr = getattr(graph, "edges", None)
        if edges_attr is not None:
            edges_in = list(edges_attr or [])

    # Allowlist + clamp
    allowed = [e for e in (edges_in or []) if canonical_edge_type((e or {}).get("type")) in policy.edge_allowlist]
    did_clip_edges = len(allowed) > policy.max_edges
    edges_out = allowed[: max(0, policy.max_edges)]

    # Build trimmed evidence (replace edges only) as a plain mapping
    if isinstance(ev, dict):
        trimmed: Any = {**ev, "graph": {**(ev.get("graph") or {}), "edges": edges_out}}
    elif hasattr(ev, "model_dump"):
        base = ev.model_dump(mode="python", by_alias=True)  # type: ignore[attr-defined]
        trimmed = {**base, "graph": {**(base.get("graph") or {}), "edges": edges_out}}
    else:
        trimmed = {"graph": {"edges": edges_out}}

    # Deterministic top events & cited IDs
    # (We clip events after deriving from the clamped edge set)
    top_all_ids = _pick_top_events(edges_out, limit=max(len(edges_out), policy.max_events) or policy.max_events)
    did_clip_events = len(top_all_ids) > policy.max_events
    top_events = top_all_ids[: max(0, policy.max_events)]

    # Anchor id from dict/attr evidence
    anchor_id = None
    if isinstance(ev, dict):
        anchor = ev.get("anchor")
        anchor_id = (anchor or {}).get("id") if isinstance(anchor, dict) else getattr(anchor, "id", None)
    else:
        anchor = getattr(ev, "anchor", None)
        anchor_id = (anchor or {}).get("id") if isinstance(anchor, dict) else getattr(anchor, "id", None)

    cited_ids = [i for i in [anchor_id, *top_events] if i][: max(0, policy.max_cited_ids)]

    # Fingerprint the effective budget config
    cfg = {
        "edge_allowlist": policy.edge_allowlist,
        "max_edges": policy.max_edges,
        "max_events": policy.max_events,
        "max_cited_ids": policy.max_cited_ids,
    }
    budget_cfg_fp = ensure_sha256_prefix(sha256_hex(canonical_json(cfg)))

    # Deterministic plan (schema-compatible with gateway.plan.json)
    truncation_action = "clip" if (did_clip_edges or did_clip_events) else "render"
    plan: Dict[str, Any] = {
        "selector_policy_id": _SELECTOR_POLICY_ID,
        "truncation_action": truncation_action,
        "budgets": {
            "max_edges": int(policy.max_edges),
            "max_events": int(policy.max_events),
            "timeout_ms": int(TIMEOUT_ENRICH_MS),
        },
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
        events=len(top_events), cited=len(cited_ids), budget_cfg_fp=budget_cfg_fp,
        truncation_action=truncation_action,
        selector_policy_id=_SELECTOR_POLICY_ID,
    )

    return plan, trimmed
