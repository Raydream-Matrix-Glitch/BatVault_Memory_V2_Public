from typing import Dict, List
from core_storage import ArangoStore
from link_utils.derive_links import derive_links               # spec §J1.5 canonical location
from core_logging import get_logger, log_stage, trace_span

logger = get_logger("ingest")

def upsert_all(
    store: ArangoStore,
    decisions: Dict[str, dict],
    events: Dict[str, dict],
    transitions: Dict[str, dict],
    alias_edges: Dict[str, dict],
    snapshot_etag: str,
) -> None:
    """
    Write (or replace) every node & edge for the current fixture batch,
    tagging each document with the batch-unique ``snapshot_etag`` so that
    stale records can be swept later.
    """

    # ------------------------------------------------------------------ #
    # 1️⃣  Derive reciprocal links *first* so attributes like
    #     `decision.supported_by` are actually stored in Arango and
    #     visible to downstream services (gateway selector, validator,
    #     golden tests).                                                #
    # ------------------------------------------------------------------ #
    log_stage(logger, "ingest", "derive_links_begin", snapshot_etag=snapshot_etag)
    derive_links(decisions, events, transitions)
    log_stage(logger, "ingest", "derive_links_completed",
              decision_count=len(decisions),
              event_count=len(events),
              transition_count=len(transitions))

    # ---------------------------  Nodes (bulk) ---------------------------
    node_docs: List[dict] = []
    for did, d in decisions.items():
        doc = dict(d); doc["_key"] = did; doc["type"] = "decision"; doc["snapshot_etag"] = snapshot_etag
        node_docs.append(doc)
    for eid, e in events.items():
        doc = dict(e); doc["_key"] = eid; doc["type"] = "event"; doc["snapshot_etag"] = snapshot_etag
        node_docs.append(doc)
    for tid, t in transitions.items():
        doc = dict(t); doc["_key"] = tid; doc["type"] = "transition"; doc["snapshot_etag"] = snapshot_etag
        node_docs.append(doc)
    inserted_nodes = 0
    try:
        inserted_nodes = store.bulk_upsert_nodes_fast(node_docs)
    except Exception:
        inserted_nodes = 0
    if not inserted_nodes:
        for d in node_docs:
            store.upsert_node(d["_key"], d["type"], d)
    
    log_stage(
        logger, "ingest", "upsert_summary",
        snapshot_etag=snapshot_etag,
        decisions=len(decisions), events=len(events), transitions=len(transitions),
    )

    # ---------------------------  Edges (bulk) ---------------------------
    edge_docs: List[dict] = []
    # LED_TO (event → decision)
    for eid, e in events.items():
        for did in e.get("led_to", []):
            edge_id = f"ledto:{eid}->{did}"
            edge_docs.append({
                "_key": edge_id,
                "_from": f"nodes/{eid}",
                "_to": f"nodes/{did}",
                "type": "LED_TO",
                "reason": None,
                "snapshot_etag": snapshot_etag,
            })
    # CAUSAL_PRECEDES (transition)
    for tid, t in transitions.items():
        if "_edge_hint" in t:
            log_stage(logger, "ingest", "transition_skipped_edge_hint", transition_id=tid)
            continue
        fr, to = t.get("from"), t.get("to")
        if fr is None or to is None:
            log_stage(logger, "ingest", "transition_missing_endpoints", transition_id=tid)
            continue
        if fr not in decisions or to not in decisions:
            log_stage(logger, "ingest", "orphan_transition_skipped", transition_id=tid, from_id=fr, to_id=to)
            continue
        edge_id = f"transition:{tid}"
        edge_docs.append({
            "_key": edge_id,
            "_from": f"nodes/{fr}",
            "_to": f"nodes/{to}",
            "type": "CAUSAL_PRECEDES",
            "relation": t.get("relation"),
            "snapshot_etag": snapshot_etag,
        })
    # ALIAS_OF (decision → event)
    _n_alias = 0
    for aid, a in (alias_edges or {}).items():
        fr, to = a.get("from"), a.get("to")
        if fr is None or to is None:
            log_stage(logger, "ingest", "alias_missing_endpoints", alias_id=aid); continue
        if fr not in decisions or to not in events:
            log_stage(logger, "ingest", "orphan_alias_skipped", alias_id=aid, from_id=fr, to_id=to); continue
        edge_id = f"alias:{aid}"
        # Projection payload: no temporal semantics; include projection fields + x-extra/tags
        payload = {"snapshot_etag": snapshot_etag}
        for k in ("title","scope","domain_from","domain_to","tags","x-extra","relation"):
            if a.get(k) is not None:
                payload[k] = a[k]
        edge_docs.append({
            "_key": edge_id,
            "_from": f"nodes/{fr}",
            "_to": f"nodes/{to}",
            "type": "ALIAS_OF",
            **payload,
        })
        _n_alias += 1
    inserted_edges = 0
    try:
        inserted_edges = store.bulk_upsert_edges_fast(edge_docs)
    except Exception:
        inserted_edges = 0
    if not inserted_edges:
        for ed in edge_docs:
            store.upsert_edge(
                ed["_key"],
                ed["_from"].split("/", 1)[1],
                ed["_to"].split("/", 1)[1],
                ed["type"],
                ed,
            )
    log_stage(logger, "ingest", "alias_upsert_summary", snapshot_etag=snapshot_etag, alias_edges=_n_alias)