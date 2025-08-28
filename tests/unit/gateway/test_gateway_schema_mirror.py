import httpx
import pytest
from fastapi.testclient import TestClient
import gateway.app as gw_app
from gateway.app import app
from types import SimpleNamespace

# ── isolate unit test from Redis ──
# ensure we stub get *and* setex (the code uses setex, not set)
app._schema_cache   = SimpleNamespace(get=lambda *_: None,
                                     setex=lambda *_: None)
gw_app._schema_cache = SimpleNamespace(get=lambda *_: None,
                                      setex=lambda *_: None)

# ---------------------------------------------------------------------------
# Stub out httpx.AsyncClient used inside gateway.app.schema_mirror
# ---------------------------------------------------------------------------

class _DummyAsyncClient:
    """Mimics the minimal API surface the route needs."""
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        # Return a deterministic fake catalog
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"title": ["title", "option"]},
            headers={"x-snapshot-etag": "dummy-etag"},
        )


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    """Force gateway to use the dummy client instead of real httpx."""
    monkeypatch.setattr(httpx, "AsyncClient", _DummyAsyncClient)


# ---------------------------------------------------------------------------
# Actual test
# ---------------------------------------------------------------------------

def test_schema_mirror_fields_route():
    client = TestClient(app)
    resp = client.get("/v2/schema/fields")
    assert resp.status_code == 200
    assert resp.json() == {"title": ["title", "option"]}
    assert resp.headers["x-snapshot-etag"] == "dummy-etag"
