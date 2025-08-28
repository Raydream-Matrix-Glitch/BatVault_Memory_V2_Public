from fastapi.testclient import TestClient
import memory_api.app as mod

def test_resolve_slug_short_circuit(monkeypatch):
    class DummyStore:
        def get_snapshot_etag(self): return "etag-3"
        def get_node(self, node_id):
            return {"_key": node_id, "type": "decision", "option": "Pause PaaS rollout"}
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    c = TestClient(mod.app)
    slug = "pause-paas-rollout-2024-q3"
    r = c.post("/api/resolve/text", json={"q": slug})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == slug
    assert body["matches"][0]["id"] == slug
    assert body["matches"][0]["title"] == "Pause PaaS rollout"
    assert body["vector_used"] is False
    assert r.headers["x-snapshot-etag"] == "etag-3"

def test_resolve_vector_flag(monkeypatch):
    class DummyStore:
        def get_snapshot_etag(self): return "etag-4"
        def resolve_text(self, q, limit=10, use_vector=False, query_vector=None):
            assert use_vector is True and query_vector is not None
            return {"query": q, "matches": [{"id":"X","score":0.9,"title":"X","type":"decision"}], "vector_used": True}
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    c = TestClient(mod.app)
    r = c.post("/api/resolve/text", json={"q":"foo", "use_vector": True, "query_vector": [0.1, 0.2, 0.3]})
    assert r.status_code == 200
    body = r.json()
    assert body["vector_used"] is True
    assert r.headers["x-snapshot-etag"] == "etag-4"

def test_resolve_slug_short_circuit_prefers_title(monkeypatch):
    """If a node provides a title, it should be used instead of option."""
    class DummyStore:
        def get_snapshot_etag(self): return "etag-3b"
        def get_node(self, node_id):
            return {"_key": node_id, "type": "decision", "option": "OptVal", "title": "Preferred Title"}
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    c = TestClient(mod.app)
    slug = "preferred-slug"
    r = c.post("/api/resolve/text", json={"q": slug})
    assert r.status_code == 200
    body = r.json()
    assert body["matches"][0]["id"] == slug
    # Title should prefer node['title']
    assert body["matches"][0]["title"] == "Preferred Title"
    assert r.headers["x-snapshot-etag"] == "etag-3b"
