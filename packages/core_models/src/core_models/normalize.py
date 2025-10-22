from __future__ import annotations
from typing import Dict, Any, Iterable, List, Tuple
from core_utils.domain import parse_anchor
from core_utils.ids import slugify_tag


NODE_TYPES = {"DECISION", "EVENT"}
EDGE_TYPES = {"LED_TO", "CAUSAL", "ALIAS_OF"}

def _upper_token(value: str) -> str:
    return (value or "").upper()

def _ensure_rfc3339_utc(ts: str) -> None:
    # keep simple check here; full format enforced by JSON Schema elsewhere
    if not isinstance(ts, str) or not ts.endswith("Z") or len(ts) < 20:
        raise ValueError("timestamp must be RFC-3339 UTC with seconds (…Z)")

def normalize_nodes(nodes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n in nodes:
        n2 = dict(n)
        n2["type"] = _upper_token(n.get("type"))
        if n2["type"] not in NODE_TYPES:
            raise ValueError(f"unknown node type: {n.get('type')!r} "
                             f"(accepted: {sorted(NODE_TYPES)})")
        # domain/id must already be canonical (lowercase); we do not auto-fix
        domain = n.get("domain")
        nid = n.get("id")
        if not isinstance(domain, str) or not isinstance(nid, str):
            raise ValueError("node 'domain' and 'id' must be strings")
        # validate timestamp shape early
        _ensure_rfc3339_utc(n.get("timestamp"))
        # validate decision_ref anchor shape if present
        dref = n.get("decision_ref")
        if dref is not None:
            if not isinstance(dref, str):
                raise ValueError("decision_ref must be a string anchor '<domain>#<id>'")
            parse_anchor(dref)  # raises with actionable text if malformed
        # optional node tags → lower-kebab via utils (fail-closed)
        if "tags" in n and n["tags"] is not None:
            if not isinstance(n["tags"], (list, tuple)):
                raise ValueError("node 'tags' must be a list of strings")
            n2["tags"] = [slugify_tag(t) for t in n["tags"]]
        out.append(n2)
    return out

def normalize_edges(edges: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in edges:
        e2 = dict(e)
        e2["type"] = _upper_token(e.get("type"))
        if e2["type"] not in EDGE_TYPES:
            raise ValueError(f"unknown edge type: {e.get('type')!r} "
                             f"(accepted: {sorted(EDGE_TYPES)})")
        # ontology v3: forbid edge tags and direction at rest
        if "tags" in e2 or "direction" in e2:
            raise ValueError("edge 'tags'/'direction' are forbidden by ontology v3")
        frm, to = e.get("from"), e.get("to")
        if not isinstance(frm, str) or not isinstance(to, str):
            raise ValueError("edge 'from' and 'to' must be strings")
        # Pure FORMAT validation (existence is checked in graph_upsert)
        try:
            parse_anchor(frm)
        except ValueError:
            raise ValueError(
                f"invalid anchor format in 'from': {frm!r} — expected '<domain>#<id>' "
                "with lowercase id; e.g., 'product#e-123'"
            )
        try:
            parse_anchor(to)
        except ValueError:
            raise ValueError(
                f"invalid anchor format in 'to': {to!r} — expected '<domain>#<id>' "
                "with lowercase id; e.g., 'product#e-123'"
            )
        _ensure_rfc3339_utc(e.get("timestamp"))
        out.append(e2)
    return out

def normalize_batch(nodes: Iterable[Dict[str, Any]], edges: Iterable[Dict[str, Any]]) -> Tuple[list, list]:
    """
    Pure, deterministic normalization. No I/O. No existence checks.
    Callers should run JSON Schema validation around this if needed.
    """
    return normalize_nodes(nodes), normalize_edges(edges)