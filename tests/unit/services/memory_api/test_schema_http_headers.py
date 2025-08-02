from fastapi.testclient import TestClient
import memory_api.app as mod

def test_schema_fields_headers(monkeypatch):
    class DummyStore:
        def get_snapshot_etag(self): return "etag-1"
        def get_field_catalog(self): return {"title": ["title", "option"]}
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    c = TestClient(mod.app)
    r = c.get("/api/schema/fields")
    assert r.status_code == 200
    assert r.json() == {"fields": {"title": ["title", "option"]}}
    assert r.headers["x-snapshot-etag"] == "etag-1"

def test_schema_rels_headers(monkeypatch):
    class DummyStore:
        def get_snapshot_etag(self): return "etag-2"
        def get_relation_catalog(self): return ["LED_TO", "CAUSAL_PRECEDES"]
    monkeypatch.setattr(mod, "store", lambda: DummyStore())
    c = TestClient(mod.app)
    r = c.get("/api/schema/rels")
    assert r.status_code == 200
    assert set(r.json()["relations"]) >= {"LED_TO", "CAUSAL_PRECEDES"}
    assert r.headers["x-snapshot-etag"] == "etag-2"
