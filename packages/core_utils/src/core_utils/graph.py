from __future__ import annotations
from typing import Any, Dict, List, Mapping
import re

def _edges_from(ev) -> List[Dict[str, Any]]:
    if hasattr(ev, "edges") and isinstance(getattr(ev, "edges", None), list):
        return [e for e in (getattr(ev, "edges") or []) if isinstance(e, dict)]
    g = getattr(ev, "graph", None)
    if hasattr(g, "edges") and isinstance(getattr(g, "edges", None), list):
        return [e for e in (getattr(g, "edges") or []) if isinstance(e, dict)]
    if isinstance(g, Mapping) and isinstance(g.get("edges"), list):
        return [e for e in g.get("edges") or [] if isinstance(e, dict)]
    d = getattr(ev, "__dict__", {}) or {}
    g = d.get("graph")
    if isinstance(g, Mapping) and isinstance(g.get("edges"), list):
        return [e for e in g.get("edges") or [] if isinstance(e, dict)]
    return []

def derive_events_from_edges(ev) -> List[Dict[str, Any]]:
    """Derive ephemeral EVENT nodes from an edges-only view (pure, deterministic).
    Root rule: events are the sources of LED_TO edges into decisions.
    No id-prefix heuristics."""
    edges = _edges_from(ev)
    pool = set(getattr(ev, "allowed_ids", []) or [])
    event_ids: set[str] = set()
    for e in edges:
        if str((e or {}).get("type") or "").upper() == "LED_TO":
            v = (e.get("from") or e.get("from_id"))
            if isinstance(v, str) and (not pool or v in pool):
                event_ids.add(v)
    def _ts_for(nid: str) -> str:
        ts = ""
        for e in edges:
            if nid in (e.get("from"), e.get("to"), e.get("from_id"), e.get("to_id")):
                t = str(e.get("timestamp") or "")
                if t and t > ts:
                    ts = t
        return ts
    return [{"id": nid, "type": "EVENT", "timestamp": _ts_for(nid)} for nid in sorted(event_ids)]

def node_ts_from_edges(ev, node_id: str) -> str:
    """Best-effort node timestamp via incident edges; empty if unknown."""
    edges = _edges_from(ev)
    ts = ""
    for e in edges:
        if node_id in (e.get("from"), e.get("to"), e.get("from_id"), e.get("to_id")):
            t = str(e.get("timestamp") or "")
            if t and t > ts:
                ts = t
    return ts

# --- Alias meta derivation (Memory wire) -------------------------------------

def alias_meta(wire_anchor_id: str, wire_edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Canonical alias summary for Memory meta; prevents cross-service drift."""
    returned = [
        e.get("from")
        for e in (wire_edges or [])
        if str(e.get("type") or "").upper() == "ALIAS_OF"
        and e.get("to") == wire_anchor_id
        and isinstance(e.get("from"), str)
    ]
    return {"partial": False, "max_depth": 1, "returned": returned}