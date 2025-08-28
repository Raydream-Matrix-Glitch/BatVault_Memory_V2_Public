from ingest.pipeline.graph_upsert import upsert_all

class DummyStore:
    def __init__(self):
        self.nodes, self.edges = {}, {}

    # API compatible with core_storage.ArangoStore
    def upsert_node(self, _id, _kind, doc): self.nodes[_id] = doc
    def upsert_edge(self, _id, _fr, _to, _kind, doc): self.edges[_id] = doc

def _sample_docs():
    d = {"id": "ship", "option": "Ship", "timestamp": "2025-01-01T00:00:00Z"}
    e = {"id": "E-1", "summary": "Bug", "timestamp": "2025-01-01T00:00:00Z", "led_to": ["ship"]}
    t = {"id": "T-1", "from": "ship", "to": "ship", "relation": "causal", "timestamp": "2025-01-01T00:00:00Z"}
    return {"ship": d}, {"E-1": e}, {"T-1": t}

def test_upsert_is_idempotent():
    store = DummyStore()
    for _ in range(2):                 # run twice
        # Provide a dummy snapshot_etag for idempotency
        upsert_all(store, *_sample_docs(), snapshot_etag="test-etag")
    assert set(store.nodes) == {"ship", "E-1", "T-1"}, \
        f"Store.nodes contained extras: {set(store.nodes) - {'ship','E-1','T-1'}}"
    assert len(store.edges) == 2       # LED_TO + CAUSAL_PRECEDES
