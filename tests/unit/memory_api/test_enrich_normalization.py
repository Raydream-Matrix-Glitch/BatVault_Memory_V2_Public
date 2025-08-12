import time
from fastapi.testclient import TestClient
import memory_api.app as mod


def test_enrich_decision_is_normalized(monkeypatch):
    class DummyStore:
        def get_enriched_decision(self, node_id):
            # Upstream document provides a title and should be preserved
            return {
                "id": node_id,
                "option": "X",
                "timestamp": "2021-01-02 03:04:05",
                "tags": ["alpha-beta", "alpha-beta"],
                "edge": "should_drop",
                "title": "Provided Title",
            }
        def get_snapshot_etag(self):
            return "etag-norm-1"

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/decision/dec1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "dec1"
    assert body["type"] == "decision"
    assert "x-extra" in body and isinstance(body["x-extra"], dict)
    assert body.get("tags") == ["alpha_beta"] or "alpha_beta" in body.get("tags", [])
    assert "edge" not in body
    assert body["title"] == "Provided Title"
    assert resp.headers.get("x-snapshot-etag") == "etag-norm-1"
    assert body["timestamp"].endswith("Z")

def test_enrich_event_is_normalized(monkeypatch):
    class DummyStore:
        def get_enriched_event(self, node_id):
            return {
                "id": node_id,
                "summary": node_id,
                "description": "Some desc.",
                "timestamp": "2021-05-06 07:08:09",
                "tags": ["m-and-a"],
                "led_to": ["d1"],
                "extra": "drop-me",
            }
        def get_snapshot_etag(self):
            return "etag-norm-2"
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/event/e1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "e1"
    assert body["type"] == "event"
    assert "x-extra" in body and isinstance(body["x-extra"], dict)
    assert body.get("tags") == ["m_and_a"] or "m_and_a" in body.get("tags", [])
    assert "extra" not in body
    assert resp.headers.get("x-snapshot-etag") == "etag-norm-2"
    assert body["timestamp"].endswith("Z")

def test_enrich_transition_is_normalized(monkeypatch):
    class DummyStore:
        def get_enriched_transition(self, node_id):
            return {
                "id": node_id,
                "from": "d1",
                "to": "d2",
                "relation": "causal",
                "reason": "Because",
                "timestamp": "2021-09-10 11:12:13",
                "tags": ["pre-pivot"],
                "edge": "drop",
            }
        def get_snapshot_etag(self):
            return "etag-norm-3"
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/transition/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "t1"
    assert body["type"] == "transition"
    assert "x-extra" in body and isinstance(body["x-extra"], dict)
    assert body.get("tags") == ["pre_pivot"] or "pre_pivot" in body.get("tags", [])
    assert "edge" not in body
    assert resp.headers.get("x-snapshot-etag") == "etag-norm-3"
    assert body["timestamp"].endswith("Z")

def test_enrich_decision_option_mirrors_into_title(monkeypatch):
    """When upstream decision lacks a title, the normaliser mirrors option into title."""
    class DummyStore:
        def get_enriched_decision(self, node_id):
            return {
                "id": node_id,
                "option": "MirrorMe",
                "timestamp": "2023-10-10 10:10:10",
                "tags": ["test-tag"],
            }
        def get_snapshot_etag(self):
            return "etag-norm-1b"

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/decision/dec2")
    assert resp.status_code == 200
    body = resp.json()
    # Option is mirrored into title
    assert body["option"] == "MirrorMe"
    assert body["title"] == "MirrorMe"
    # Title should be present even though upstream lacked it
    assert "title" in body
    assert resp.headers.get("x-snapshot-etag") == "etag-norm-1b"
    assert body["timestamp"].endswith("Z")