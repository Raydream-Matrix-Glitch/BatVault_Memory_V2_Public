"""
Validates the Gateway's read-through schema mirror
(`/v2/schema/{fields|rels}`) without hitting a live Memory-API.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import app


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
