from typing import Dict
from core_storage import ArangoStore
from link_utils.derive_links import derive_links               # spec §J1.5 canonical location
from core_logging import log_stage, trace_span


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

    # ---------------------------  Nodes  ---------------------------
    for did, d in decisions.items():
        doc = dict(d)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(did, "decision", doc)

    for eid, e in events.items():
        doc = dict(e)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(eid, "event", doc)

    for tid, t in transitions.items():
        doc = dict(t)
        doc["snapshot_etag"] = snapshot_etag
        store.upsert_node(tid, "transition", doc)

    # ---------------- back-link derivation (LED_TO / SUPPORTED_BY etc.) ----------------
    log_stage("ingest", "derive_links_begin", snapshot_etag=snapshot_etag)
    derive_links(decisions, events, transitions)
    log_stage("ingest", "derive_links_completed",
              decision_count=len(decisions),
              event_count=len(events),
              transition_count=len(transitions))

    # ---------------------------  Edges  ---------------------------
    # LED_TO  (event → decision)
    for eid, e in events.items():
        for did in e.get("led_to", []):
            edge_id = f"ledto:{eid}->{did}"
            payload = {"reason": None, "snapshot_etag": snapshot_etag}
            store.upsert_edge(edge_id, eid, did, "LED_TO", payload)

    # CAUSAL_PRECEDES  (transition)
    for tid, t in transitions.items():
        fr, to = t["from"], t["to"]
        if fr not in decisions or to not in decisions:          # spec §P orphan tolerance
            log_stage("ingest", "orphan_transition_skipped",
                      transition_id=tid, from_id=fr, to_id=to)
            continue
        edge_id = f"transition:{tid}"
        payload = {"relation": t.get("relation"), "snapshot_etag": snapshot_etag}
        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
