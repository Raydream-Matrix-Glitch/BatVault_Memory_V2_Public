from core_storage import ArangoStore

def upsert_all(store: ArangoStore, decisions: dict, events: dict, transitions: dict) -> None:
    # Nodes
    for did, d in decisions.items():
        store.upsert_node(did, "decision", d)
    for eid, e in events.items():
        store.upsert_node(eid, "event", e)
    for tid, t in transitions.items():
        store.upsert_node(tid, "transition", t)

    # Edges (LED_TO from event to decision)
    for eid, e in events.items():
        for did in e.get("led_to", []):
            if did in decisions:
                edge_id = f"ledto:{eid}->{did}"
                store.upsert_edge(edge_id, eid, did, "LED_TO", {"reason": None})

    # Edges (CAUSAL_PRECEDES) for transitions
    for tid, t in transitions.items():
        fr, to = t["from"], t["to"]
        if fr in decisions and to in decisions:
            edge_id = f"transition:{tid}"
            store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", {"relation": t.get("relation")})
