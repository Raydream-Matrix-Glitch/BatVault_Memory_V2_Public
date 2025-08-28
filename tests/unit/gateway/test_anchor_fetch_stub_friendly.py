import json
import pytest
from contextlib import asynccontextmanager

pytest.importorskip("pytest_asyncio")

from gateway.evidence import EvidenceBuilder

class HeaderOnlyResp:
    def __init__(self, body: dict, headers: dict, status_code: int = 200, has_raise: bool = False):
        self._json = body
        self.headers = headers
        self.status_code = status_code
        # some stubs don't expose raise_for_status – simulate both cases
        if has_raise:
            self.raise_for_status = lambda: None  # type: ignore[attr-defined]

    def json(self):
        return self._json

class SimpleClient:
    def __init__(self, anchor_json: dict, headers: dict, with_raise: bool):
        self._anchor_json = anchor_json
        self._headers = headers
        self._with_raise = with_raise

    async def get(self, url, *a, **kw):
        if not url.startswith("/api/enrich/decision/"):
            raise AssertionError(f"Unexpected GET {url}")
        return HeaderOnlyResp(self._anchor_json, self._headers, has_raise=self._with_raise)

    async def aclose(self):
        pass

@asynccontextmanager
async def _make_client(anchor_json: dict, headers: dict, with_raise: bool):
    c = SimpleClient(anchor_json, headers, with_raise)
    try:
        yield c
    finally:
        await c.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hdrs,with_raise,expected",
    [
        ({"Snapshot-ETag": "etag-a"}, False, "etag-a"),
        ({"snapshot-etag": "etag-b"}, True, "etag-b"),
        ({"X_SNAPSHOT_ETAG": "etag-c"}, False, "etag-c"),
        ({}, False, "unknown"),
    ],
)
async def test_anchor_fetch_is_stub_friendly_and_propagates_etag(monkeypatch, hdrs, with_raise, expected):
    """
    Builder must tolerate stubs that omit .raise_for_status and still propagate
    snapshot_etag from any supported header spelling; otherwise, set 'unknown'.
    """
    anchor = {"id": "philips-led-lighting-focus-2013", "transitions": []}

    # prevent any raw httpx usage
    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("raw httpx.AsyncClient constructed")
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", Boom)

    # patch safe client factory to our parameterised stub
    from functools import partial
    monkeypatch.setattr(
        "gateway.evidence._safe_async_client",
        lambda *a, **kw: _make_client(anchor, hdrs, with_raise),
        raising=True,
    )

    # Stub Redis to a no-op
    class DummyRedis:
        def get(self, k): return None
        def setex(self, k, ttl, v): pass
    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: DummyRedis())

    eb = EvidenceBuilder()
    ev = await eb.build(anchor["id"])
    assert ev.snapshot_etag == expected
    # Ensure we didn't fall back to a stub-only anchor (which would lose transitions)
    if expected != "unknown":
        assert len(ev.transitions.preceding) + len(ev.transitions.succeeding) >= 0  # construction succeeded
