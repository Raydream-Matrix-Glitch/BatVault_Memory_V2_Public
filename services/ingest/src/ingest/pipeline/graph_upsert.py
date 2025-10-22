from __future__ import annotations

from typing import List, Dict, Tuple, Set
from collections import deque
from core_storage import ArangoStore
from core_logging import get_logger, log_stage, trace_span
from core_models.ontology import edge_id, make_anchor
from core_utils.domain import parse_anchor
from core_validator import validate_node, validate_edge

logger = get_logger("ingest.upsert")

def _validate_edge_endpoints_exist(edge: dict, nodes_by_anchor: Dict[str, dict]) -> None:
    """
    Existence check (format already validated upstream by core_models.normalize).
    Raises ValueError with actionable messages.
    """
    for side in ("from", "to"):
        raw = edge.get(side)
        # Guard in case normalizer was bypassed:
        try:
            parse_anchor(raw)
        except ValueError as ex:
            raise ValueError(
                f"invalid anchor format in '{side}': {raw!r} — expected '<domain>#<id>' "
                "with lowercase id; e.g., 'product#e-123'"
            ) from ex
        if raw not in nodes_by_anchor:
            raise ValueError(
                f"unknown '{side}' anchor: {raw!r} (node not found in batch or store)"
            )

def _enforce_edge_domain_policy(e: dict, nodes_by_anchor: Dict[str, dict]) -> None:
    """LED_TO/CAUSAL must be same-domain; ALIAS_OF may cross domain.
    For ALIAS_OF, if edge.domain is present it MUST equal the alias event's domain (the 'from' node).
    """
    t = e.get("type")
    if t in ("LED_TO","CAUSAL"):
        frm, to = e.get("from"), e.get("to")
        nf, nt = nodes_by_anchor.get(frm), nodes_by_anchor.get(to)
        if nf and nt and nf.get("domain") != nt.get("domain"):
            raise ValueError(f"edge {t} must connect same-domain nodes: {frm} -> {to}")
    elif t == "ALIAS_OF":
        dom = e.get("domain")
        if dom:
            ev = nodes_by_anchor.get(e.get("from") or "") or {}
            ev_dom = ev.get("domain")
            if ev_dom and ev_dom != dom:
                raise ValueError(f"ALIAS_OF.domain must equal alias event domain (got {dom}, expected {ev_dom})")

def _recompute_edge_id(e: dict) -> dict:
    kind = e.get("type"); frm, to = e.get("from"), e.get("to")
    e["id"] = edge_id(kind, frm, to)
    return e

def _build_alias_edges(nodes: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Build ALIAS_OF edges from Event.decision_ref ("<domain>#<decision_id>").
    Direction: alias EVENT → home DECISION (Baseline §2.2, §3). Set:
      • edge.id        = edge_id(type, from, to)
      • edge.timestamp = event.timestamp
      • edge.domain    = event.domain
    No schema validation here (single gate later). Returns (edges, rejected)."""
    built: List[dict] = []
    rejected: List[dict] = []
    for n in nodes:
        if n.get("type") != "EVENT":
            continue
        dec_anchor = n.get("decision_ref")
        if not dec_anchor:
            continue
        if not n.get("timestamp"):
            rejected.append({"event": n.get("id"), "reason": "alias_event_missing_timestamp"})
            continue
        try:
            # validate anchors
            parse_anchor(dec_anchor)
            ev_anchor = make_anchor(n["domain"], n["id"])
            # v3: alias EVENT (from) → home DECISION (to)
            e = {
                "type": "ALIAS_OF",
                "from": ev_anchor,
                "to": dec_anchor,
                "timestamp": n["timestamp"],
                "domain": n.get("domain"),
            }
            e = _recompute_edge_id(e)
            # No schema validation here: single strict gate happens later.
            built.append(e)
        except (ValueError, KeyError) as ex:
            rejected.append({"event": n.get("id"), "reason": str(ex)})
    return built, rejected

def _index_nodes(nodes: List[dict], *, snapshot_etag: str | None = None) -> Dict[str, dict]:
    m: Dict[str, dict] = {}
    for n in nodes:
        try:
            a = make_anchor(n["domain"], n["id"])
            m[a] = n
        except ValueError as e:
            log_stage(
                logger, "ingest", "index_nodes_failed",
                error=str(e), node_id=n.get("id"), snapshot_etag=snapshot_etag
            )
            raise
    return m

def _inherit_sensitivity(
    clean_nodes: List[dict],
    edges: List[dict],
    *, snapshot_etag: str | None = None
) -> Tuple[List[dict], int]:
    """If Event.sensitivity is missing and Event connects to ≥1 Decisions
    (via LED_TO/CAUSAL or decision_ref/ALIAS_OF), set to the most restrictive
    value per configured ordering. Record provenance in x-extra.sensitivity_inheritance."""
    by_anchor: Dict[str, dict] = {}
    for n in clean_nodes:
        try:
            by_anchor[make_anchor(n["domain"], n["id"])] = n
        except ValueError as e:
            log_stage(
                logger, "ingest", "inherit_sensitivity_failed",
                error=str(e), node_id=n.get("id"), snapshot_etag=snapshot_etag
            )
            raise
    # Map each EVENT anchor → connected DECISION anchors (both causal and alias)
    decisions_by_event: Dict[str, Set[str]] = {}
    for e in edges or []:
        t, frm, to = (e or {}).get("type"), (e or {}).get("from"), (e or {}).get("to")
        if t in ("LED_TO", "CAUSAL"):
            if (by_anchor.get(frm, {}).get("type") == "EVENT") and (by_anchor.get(to, {}).get("type") == "DECISION"):
                decisions_by_event.setdefault(frm, set()).add(to)
            if (by_anchor.get(to, {}).get("type") == "EVENT") and (by_anchor.get(frm, {}).get("type") == "DECISION"):
                decisions_by_event.setdefault(to, set()).add(frm)
        elif t == "ALIAS_OF":
            # alias EVENT (from) → home DECISION (to)
            decisions_by_event.setdefault(frm, set()).add(to)
    # Policy ordering (typed settings first; env fallback). Higher index == more restrictive.
    try:
        from core_config import get_settings  # typed, centralised config
        _cfg = get_settings()
        ordering = list(getattr(_cfg, "sensitivity_order", [])) or []
    except (ImportError, AttributeError, RuntimeError, ValueError, TypeError):
        ordering = []
    if not ordering:
        import os as _os
        ordering = [x.strip() for x in _os.getenv("SENSITIVITY_ORDER", "low,medium,high").split(",") if x.strip()]
    rank = {v: i for i, v in enumerate(ordering)}
    def pick(values: List[str]) -> str | None:
        scored = [(rank.get(v, 10**9), v) for v in values if v is not None]
        if not scored:
            return None
        scored.sort(reverse=True)
        return scored[0][1]
    # Apply inheritance
    updated: List[dict] = []
    applied = 0
    for n in clean_nodes:
        if n.get("type") != "EVENT" or n.get("sensitivity"):
            continue
        a = make_anchor(n.get("domain"), n.get("id"))
        considered = sorted(decisions_by_event.get(a, set()))
        if not considered:
            continue
        vals = {da: (by_anchor.get(da) or {}).get("sensitivity") for da in considered}
        chosen = pick([vals[k] for k in considered if vals.get(k) is not None])
        if not chosen:
            continue
        nn = dict(n)
        nn["sensitivity"] = chosen
        x = dict(nn.get("x-extra") or {})
        x["sensitivity_inheritance"] = {
            "rule": "most_restrictive",
            "ordering": ordering,
            "decisions_considered": considered,
            "values": vals,
            "selected": chosen,
        }
        nn["x-extra"] = x
        updated.append(nn); applied += 1
    if not applied:
        return clean_nodes, 0
    # merge updates into the full node list
    idx = {(n["domain"], n["id"]): n for n in clean_nodes}
    for nn in updated:
        idx[(nn["domain"], nn["id"])] = nn
    return list(idx.values()), applied

def upsert_pipeline(store: ArangoStore, nodes: List[dict], edges: List[dict], *, snapshot_etag: str | None = None) -> Dict[str, any]:
    """Normalize (done upstream) → Build aliases → Inherit sensitivity → Validate once → Write."""
    log_stage(
        logger, "ingest", "pipeline_start",
        snapshot_etag=snapshot_etag, node_count=len(nodes), edge_count=len(edges)
    )
    # 1) Build ALIAS_OF from decision_ref
    alias_edges, alias_rejected = _build_alias_edges(nodes)
    # 2) Combine all edges & enforce domain policy + deterministic ids
    nodes_by_anchor = _index_nodes(nodes, snapshot_etag=snapshot_etag)
    all_edges: List[dict] = []
    for e in list(edges) + alias_edges:
        kind = e.get("type"); frm, to = e.get("from"), e.get("to")
        if not (kind and frm and to):
            raise ValueError("edge missing required fields")
        _enforce_edge_domain_policy(e, nodes_by_anchor)
        _validate_edge_endpoints_exist(e, nodes_by_anchor)
        e = _recompute_edge_id(e)
        all_edges.append(e)
    log_stage(
        logger, "ingest", "aliases_built",
        snapshot_etag=snapshot_etag,
        built=len(alias_edges), rejected=len(alias_rejected),
        sample_alias_ids=[e.get("id") for e in alias_edges[:3]],
    )
    # 3) Sensitivity inheritance (most restrictive across connected decisions)
    updated_nodes, applied = _inherit_sensitivity(
        nodes, all_edges, snapshot_etag=snapshot_etag
    )
    # 4) Strict validation gate AFTER alias & inheritance — aggregate errors for a clean summary
    node_errors: List[Dict[str,str]] = []
    valid_nodes: List[dict] = []
    for n in updated_nodes:
        ok, errs = validate_node(n)
        if ok:
            valid_nodes.append(n)
        else:
            node_errors.append({"id": n.get("id"), "type": n.get("type"), "error": (errs[0] if errs else "unknown")})
    edge_errors: List[Dict[str,str]] = []
    valid_edges: List[dict] = []
    for e in all_edges:
        ok, errs = validate_edge(e)
        if ok:
            valid_edges.append(e)
        else:
            edge_errors.append({"id": e.get("id"), "type": e.get("type"), "error": (errs[0] if errs else "unknown")})
    # Always emit a compact validation summary (big-run friendly)
    log_stage(
        logger, "ingest", "validate_summary", snapshot_etag=snapshot_etag,
        nodes_checked=len(updated_nodes), nodes_invalid=len(node_errors),
        edges_checked=len(all_edges),  edges_invalid=len(edge_errors),
        sample_node_error=(node_errors[0] if node_errors else None),
        sample_edge_error=(edge_errors[0] if edge_errors else None),
    )
    if node_errors or edge_errors:
        # Fail-closed per Baseline, but with one clear error and actionable samples
        raise ValueError(f"validation failed: nodes_invalid={len(node_errors)}, edges_invalid={len(edge_errors)}")
    log_stage(
        logger, "ingest", "validated",
        snapshot_etag=snapshot_etag, node_count=len(valid_nodes), edge_count=len(valid_edges)
    )
    # 5) Writes
    with trace_span("ingest.upsert.nodes", stage="ingest"):
        summ_nodes = store.upsert_nodes(valid_nodes, snapshot_etag=snapshot_etag)
    with trace_span("ingest.upsert.edges", stage="ingest"):
        summ_edges = store.upsert_edges(valid_edges, snapshot_etag=snapshot_etag)
    log_stage(
        logger, "ingest", "upsert_summary",
        nodes_in=len(valid_nodes),
        nodes_written=summ_nodes.get("written", 0),
        nodes_rejected=summ_nodes.get("rejected", 0),
        edges_in=len(valid_edges),
        edges_written=summ_edges.get("written", 0),
        edges_rejected=summ_edges.get("rejected", 0),
        error_count=len((summ_nodes.get("errors") or [])) + len((summ_edges.get("errors") or [])),
        failed_ids=[e.get("doc_id") for e in (summ_nodes.get("errors") or [])[:5]]
                  + [e.get("doc_id") for e in (summ_edges.get("errors") or [])[:5]],
        sensitivity_inherited=applied,
    )
    return {"nodes": summ_nodes, "edges": summ_edges, "alias_rejected": alias_rejected, "sensitivity_applied": applied}