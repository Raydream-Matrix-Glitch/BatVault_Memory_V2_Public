from __future__ import annotations
from typing import Optional, Mapping, Any, Iterable
from core_models.ontology import CAUSAL_EDGE_TYPES, canonical_edge_type

ORIENT_PRECEDING = "preceding"
ORIENT_SUCCEEDING = "succeeding"
_ALLOWED = {ORIENT_PRECEDING, ORIENT_SUCCEEDING}
_CAUSAL_TYPES = set(CAUSAL_EDGE_TYPES)

def assert_ready_for_orientation(graph: Mapping[str, Any]) -> None:
    """
    Precondition guard to enforce call-order:
    Orientation runs only after Memory's view has been validated and the
    Gateway's evidence has been de-duplicated/filtered (Baseline §5).

    Rules we assert here (edges-only, strict keys presence):
      * graph is a mapping with 'edges' list
      * each edge is a mapping with required keys: type, from|from_id, to|to_id, timestamp
      * no legacy 'rel' field present (Memory never emits it; Baseline §2.2.1)
    Any violation raises ValueError to fail-closed.
    """
    if not isinstance(graph, Mapping):
        raise ValueError("orientation_precondition_failed: graph must be a mapping")
    edges = graph.get("edges")
    if not isinstance(edges, Iterable):
        raise ValueError("orientation_precondition_failed: graph.edges must be an iterable")
    for i, e in enumerate(edges or []):
        if not isinstance(e, Mapping):
            raise ValueError(f"orientation_precondition_failed: edge[{i}] not a mapping")
        if "rel" in e:
            raise ValueError("orientation_precondition_failed: legacy 'rel' field present")
        et = e.get("type")
        f  = e.get("from") or e.get("from_id")
        t  = e.get("to")   or e.get("to_id")
        ts = e.get("timestamp")
        if et is None or f is None or t is None or ts is None:
            raise ValueError(f"orientation_precondition_failed: edge[{i}] missing required keys")

def classify_edge_orientation(
    anchor_id: str,
    edge: Mapping[str, Any],
    hinted: Optional[str] = None,
) -> Optional[str]:
    """
    Decide orientation of an edge relative to the anchor.
    Rules (Baseline §5 Orientation Writer):
      - Only causal edges ({LED_TO, CAUSAL}) get an orientation; ALIAS_OF stays neutral.
      - A valid `hinted` orientation may be applied **only** to causal edges
        (e.g., alias-tail succeeding relative to the anchor; see Baseline §5).
      - If edge.to == anchor → preceding; if edge.from == anchor → succeeding.
      - Otherwise None.
    """
    try:
        try:
            et = canonical_edge_type(edge.get("type"))
        except ValueError:
            return None  # unknown → no orientation
        # Never orient non-causal edges (incl. ALIAS_OF), even if a hint is supplied.
        # Baseline §§2.2.1, 5.
        if et not in _CAUSAL_TYPES:
            return None
        # Apply hints only for causal edges (alias-tail succeeds). Baseline §5.
        if hinted in _ALLOWED:
            return hinted
        t_from = edge.get("from") or edge.get("from_id")
        t_to   = edge.get("to")   or edge.get("to_id")
        if t_to == anchor_id:
            return ORIENT_PRECEDING
        if t_from == anchor_id:
            return ORIENT_SUCCEEDING
        return None
    except (AttributeError, KeyError, TypeError):
        # Fail closed — unknown/ill-typed edge structure ⇒ no orientation. Baseline §5.
        return None