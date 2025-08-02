import time
from fastapi.testclient import TestClient
import memory_api.app as mod

def test_expand_candidates_timeout(monkeypatch):
    class SlowStore:
        def get_snapshot_etag(self): return "etag-5"
        def expand_candidates(self, anchor, k=1):
            time.sleep(1.0)  # > 0.25s
            return {"anchor": anchor, "neighbors": []}
    monkeypatch.setattr(mod, "store", lambda: SlowStore())
    c = TestClient(mod.app)
    r = c.post("/api/graph/expand_candidates", json={"anchor":"x","k":1})
    assert r.status_code == 504
    assert r.json()["detail"] == "timeout"

def test_resolve_text_timeout(monkeypatch):
    class SlowStore:
        def get_snapshot_etag(self): return "etag-6"
        def resolve_text(self, *args, **kw):
            time.sleep(1.0)  # > 0.8s
            return {"query":"x","matches":[],"vector_used":False}
    monkeypatch.setattr(mod, "store", lambda: SlowStore())
    c = TestClient(mod.app)
    r = c.post("/api/resolve/text", json={"q":"x"})
    assert r.status_code == 504
    assert r.json()["detail"] == "timeout"
