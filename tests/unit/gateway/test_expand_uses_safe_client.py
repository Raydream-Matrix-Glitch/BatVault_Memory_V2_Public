import asyncio
import pytest
from contextlib import asynccontextmanager

pytest.importorskip("pytest_asyncio")  # skip if pytest-asyncio not available

import gateway.evidence as evmod


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def get(self, url, *a, **kw):
        # Not expected in this test
        raise AssertionError(f"GET should not be called in expand_graph: {url}")

    async def post(self, url, *a, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        class Resp:
            status_code = 200
            def json(self):
                # Return a minimal, valid payload
                return {"neighbors": [], "meta": {"ok": True}}
            def raise_for_status(self):
                pass
        return Resp()

    async def aclose(self):
        pass


@asynccontextmanager
async def _fake_safe_async_client(*a, **kw):
    client = RecordingClient()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_expand_graph_uses_safe_client_and_relative_path(monkeypatch):
    """
    expand_graph() must use _safe_async_client and post to the relative
    /api/graph/expand_candidates path so test stubs can intercept it.
    It must not construct a raw httpx.AsyncClient directly.
    """
    # If code tries to construct a raw httpx.AsyncClient, explode loudly.
    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("expand_graph constructed raw httpx.AsyncClient")

    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", Boom)
    monkeypatch.setattr(evmod, "_safe_async_client", _fake_safe_async_client, raising=True)

    out = await evmod.expand_graph("dec-123", k=2)
    assert out == {"neighbors": [], "meta": {"ok": True}}

    # Validate that our recording client saw exactly one POST to the relative path
    # Our fake client instance is inaccessible here, so re-patch and trigger again,
    # capturing the instance via closure.
    seen = {}
    class RecordingClient2(RecordingClient):
        async def post(self, url, *a, **kw):
            seen["call"] = ("POST", url, kw.get("json"))
            return await super().post(url, *a, **kw)

    @asynccontextmanager
    async def _fake_safe_async_client2(*a, **kw):
        client = RecordingClient2()
        try:
            yield client
        finally:
            await client.aclose()

    monkeypatch.setattr(evmod, "_safe_async_client", _fake_safe_async_client2, raising=True)
    await evmod.expand_graph("dec-xyz", k=5)
    assert seen["call"][0] == "POST"
    assert seen["call"][1] == "/api/graph/expand_candidates"
    assert seen["call"][2] == {"node_id": "dec-xyz", "k": 5}
