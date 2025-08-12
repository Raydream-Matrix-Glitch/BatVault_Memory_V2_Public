import pytest
from fastapi.testclient import TestClient

import memory_api.app as mod


def test_resolve_text_honours_use_vector_false(monkeypatch):
    """
    If the caller sends use_vector=false explicitly, the endpoint must not
    auto-embed. This test makes embeddings raise if called; success implies
    the code path did not attempt to embed and returns vector_used=False.
    """
    # Enable embeddings globally to ensure only the explicit False suppresses it
    monkeypatch.setenv("ENABLE_EMBEDDINGS", "true")

    async def boom(*args, **kwargs):
        raise AssertionError("embed() should not be called when use_vector=false")

    monkeypatch.setattr("memory.embeddings_client.embed", boom, raising=True)

    client = TestClient(mod.app)
    resp = client.post("/api/resolve/text", json={"q": "panasonic plasma exit", "use_vector": False, "limit": 3})
    assert resp.status_code in (200, 504)  # 504 only if store backend times out; both imply no embed
    body = resp.json()
    assert body.get("vector_used") is False