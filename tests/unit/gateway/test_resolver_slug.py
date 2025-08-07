import asyncio
from types import SimpleNamespace

import gateway.resolver as r
import gateway.resolver as _resolver_module


# ---------------------------------------------------------------------------
# Stub external deps: Redis + Memory-API HTTP call
# ---------------------------------------------------------------------------
async def _async_noop(*_a, **_k):  # make setex awaitable to match prod interface
    return None
r._redis = SimpleNamespace(get=lambda *_: None, setex=lambda *_: None)


class _DummyResp:  # minimal stand-in for httpx.Response
    status_code = 200

    def json(self):
        return {"id": "foo-bar-2020", "option": "dummy"}


class _DummyClient:
    def __init__(self, *a, **kw):
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        ...

    async def get(self, *_a, **_kw):
        return _DummyResp()


r.httpx.AsyncClient = _DummyClient  # type: ignore

# ---------------------------------------------------------------------------

_bm25_called = {"flag": False}

async def _bm25_marker(*a, **k):
    # If the slug fast path is working, this should never be invoked.
    _bm25_called["flag"] = True
    return []  # return empty matches if somehow invoked, to keep test deterministic

_orig_bm25 = _resolver_module.search_bm25
_resolver_module.search_bm25 = _bm25_marker  # type: igno


async def _run():
    result = await r.resolve_decision_text("foo-bar-2020")
    assert result["id"] == "foo-bar-2020"


def test_slug_fast_path():
    # Ensure BM25 is not called when a canonical slug is provided
    orig_bm25 = _resolver_module.search_bm25

    async def _fail_if_called(*a, **k):
        raise AssertionError("BM25 path should not be called for slug input")

    _resolver_module.search_bm25 = _fail_if_called  # type: ignore
    try:
        asyncio.run(_run())
        # Ensure BM25 was not used on a canonical slug
        assert _bm25_called["flag"] is False
    finally:
        _resolver_module.search_bm25 = _orig_bm25  # restore
