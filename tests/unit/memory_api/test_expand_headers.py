import memory_api.app as mod
from fastapi.testclient import TestClient


def test_expand_headers(monkeypatch):
    """Ensure /api/graph/expand_candidates echoes snapshot ETag in headers."""
    class DummyStore:
        def get_snapshot_etag(self):
            return "etag-10"

        def expand_candidates(self, node_id: str, k: int = 1):
            return {"node_id": node_id, "neighbors": []}

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    res = client.post("/api/graph/expand_candidates", json={"node_id": "node-x", "k": 1})
    assert res.status_code == 200
    # Header should reflect the snapshot_etag from the store
    assert res.headers["x-snapshot-etag"] == "etag-10"
    # Body meta.snapshot_etag should also reflect the same value
    body = res.json()
    assert isinstance(body.get("meta"), dict)
    assert body["meta"].get("snapshot_etag") == "etag-10"


def test_expand_flatten_neighbors(monkeypatch):
    """Legacy shape {'events': …, 'transitions': …} must be flattened."""
    class DummyStore:
        def get_snapshot_etag(self):
            return "etag-11"

        # Milestone-4 contract: store still receives (node_id, k)
        def expand_candidates(self, node_id: str, k: int = 1):
            return {
                "node_id": node_id,
                "neighbors": {
                    "events": [{"id": "e1"}],
                    "transitions": [{"id": "t1"}],
                },
            }

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    res = client.post("/api/graph/expand_candidates", json={"node_id": "node-y", "k": 1})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["neighbors"], list)
    ids = {n["id"] for n in body["neighbors"]}
    # meta.snapshot_etag should be returned alongside neighbours
    assert isinstance(body.get("meta"), dict)
    assert body["meta"].get("snapshot_etag") == "etag-11"


def test_resolve_empty_query(monkeypatch):
    """Empty query with no vector parameters should return empty contract."""
    class DummyStore:
        def get_snapshot_etag(self):
            return "etag-12"

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    res = client.post("/api/resolve/text", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["matches"] == []
    assert body["vector_used"] is False