import time
from fastapi.testclient import TestClient
import memory_api.app as mod


def test_enrich_decision_happy_path(monkeypatch):
    """Ensure a successful enrichment returns the document and ETag."""
    class DummyStore:
        def get_enriched_decision(self, node_id):
            # return a simple document for the test
            return {"id": node_id, "option": "OK"}
        def get_snapshot_etag(self):
            return "etag-enrich-1"

    # Patch the module-level store() to return our DummyStore
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/decision/abc")
    assert resp.status_code == 200
    # Body should include original fields plus a meta block with snapshot_etag
    body = resp.json()
    assert body.get("id") == "abc"
    assert body.get("option") == "OK"
    assert isinstance(body.get("meta"), dict)
    assert body["meta"].get("snapshot_etag") == "etag-enrich-1"
    # The snapshot ETAG must be propagated to the response header as well
    assert resp.headers["x-snapshot-etag"] == "etag-enrich-1"


def test_enrich_decision_not_found(monkeypatch):
    """Missing documents should yield a 404 response with the correct detail."""
    class DummyStore:
        def get_enriched_decision(self, node_id):
            return None  # simulate unknown slug
        def get_snapshot_etag(self):
            return "etag-enrich-2"

    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/decision/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "decision_not_found"


def test_enrich_decision_timeout(monkeypatch):
    """Operations exceeding 0.6s should return a 504 timeout."""
    class SlowStore:
        def get_enriched_decision(self, node_id):
            time.sleep(1.0)  # longer than the 0.6s timeout enforced by the API
            return {"id": node_id}
        def get_snapshot_etag(self):
            return "etag-enrich-3"

    monkeypatch.setattr(mod, "store", lambda: SlowStore())
    client = TestClient(mod.app)
    resp = client.get("/api/enrich/decision/timeout")
    assert resp.status_code == 504
    body = resp.json()
    assert body["detail"] == "timeout"