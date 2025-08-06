from ingest.pipeline.graph_upsert import upsert_all


class DummyStore:
    def __init__(self):
        self.nodes = {}
        self.edges = {}

    # node + edge upsert mocks ---------------------------------------------
    def upsert_node(self, _id, _kind, doc):
        self.nodes[_id] = doc

    def upsert_edge(self, _id, _from, _to, _kind, doc):
        self.edges[_id] = (_from, _to, _kind)


def _sample_docs():
    decision = {
        "id": "foo-dec",
        "option": "Foo",
        "timestamp": "2024-01-01T00:00:00Z",
        "supported_by": [],
        "based_on": [],
        "transitions": [],
    }
    event = {
        "id": "bar-ev",
        "summary": "Bar",
        "timestamp": "2024-01-02T00:00:00Z",
        "led_to": ["foo-dec"],
    }
    transition = {
        "id": "baz-tr",
        "from": "foo-dec",
        "to": "foo-dec",
        "relation": "causal",
        "reason": "self-loop",
        "timestamp": "2024-01-03T00:00:00Z",
    }
    return {"foo-dec": decision}, {"bar-ev": event}, {"baz-tr": transition}


def test_edges_upserted():
    store = DummyStore()
    d, e, t = _sample_docs()
    upsert_all(store, d, e, t, snapshot_etag="snap123")  # should not raise
    # sanity: derive_links must NOT have introduced edge-hint pseudo-nodes
    offending = [tid for tid, doc in t.items() if "_edge_hint" in doc]
    assert not offending, f"Unexpected _edge_hint docs leaked: {offending}"
    assert "ledto:bar-ev->foo-dec" in store.edges
    assert "transition:baz-tr" in store.edges