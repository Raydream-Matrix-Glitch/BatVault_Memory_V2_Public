from typing import Dict
from core_storage import ArangoStore


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
        edge_id = f"transition:{tid}"
        payload = {"relation": t.get("relation"), "snapshot_etag": snapshot_etag}
        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
