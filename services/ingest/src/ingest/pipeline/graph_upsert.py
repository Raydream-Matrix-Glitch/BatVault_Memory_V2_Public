from typing import Dict
from core_storage import ArangoStore
from link_utils.derive_links import derive_links               # spec §J1.5 canonical location
from core_logging import get_logger, log_stage, trace_span

logger = get_logger("ingest")

def upsert_all(
    store: ArangoStore,
    decisions: Dict[str, dict],
    events: Dict[str, dict],
    transitions: Dict[str, dict],
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

    # ---------------------------  Nodes  ---------------------------
    # Decisions
    _n_d, _n_e, _n_t = 0, 0, 0
    for did, d in decisions.items():
        doc = dict(d)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(did, "decision", doc)
        _n_d += 1
    # Events
    for eid, e in events.items():
        doc = dict(e)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(eid, "event", doc)
        _n_e += 1
    # Transitions (edges)
    for tid, t in transitions.items():
        doc = dict(t)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(tid, "transition", doc)
        _n_t += 1
    
    log_stage(
        logger, "ingest", "upsert_summary",
        snapshot_etag=snapshot_etag,
        decisions=_n_d, events=_n_e, transitions=_n_t,
    )

    # ---------------------------  Edges  ---------------------------
    # LED_TO  (event → decision)
    for eid, e in events.items():
        for did in e.get("led_to", []):
            edge_id = f"ledto:{eid}->{did}"
            payload = {"reason": None, "snapshot_etag": snapshot_etag}
            store.upsert_edge(edge_id, eid, did, "LED_TO", payload)

    # CAUSAL_PRECEDES  (transition)
    for tid, t in transitions.items():
        # Skip synthetic or malformed entries (future-proofing)
        if "_edge_hint" in t:
            log_stage(logger, "ingest", "transition_skipped_edge_hint", transition_id=tid)
            continue
        fr, to = t.get("from"), t.get("to")
        if fr is None or to is None:
            log_stage(logger, "ingest", "transition_missing_endpoints", transition_id=tid)
            continue
        if fr not in decisions or to not in decisions:          # spec §P orphan tolerance
            log_stage(logger, "ingest", "orphan_transition_skipped",
                      transition_id=tid, from_id=fr, to_id=to)
            continue
        edge_id = f"transition:{tid}"
        payload = {"relation": t.get("relation"), "snapshot_etag": snapshot_etag}
        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
