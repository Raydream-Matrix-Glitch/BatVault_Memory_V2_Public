from fastapi.testclient import TestClient
import httpx
from memory_api.app import app

def test_healthz():
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json().get("ok") is True

class _DummyAC:
    def __init__(self, *args, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def get(self, url): 
        class R:
            status_code = 200
            headers = {}
            def json(self): return {"version":"3.11"}
        return R()

def test_readyz(monkeypatch):
    # Make /readyz think Arango is up
    monkeypatch.setattr(httpx, "AsyncClient", _DummyAC)
    c = TestClient(app)
    r = c.get("/readyz")
    assert r.status_code == 200
    assert r.json().get("ready") is True