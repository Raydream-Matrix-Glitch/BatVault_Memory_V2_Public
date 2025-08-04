import asyncio
from types import SimpleNamespace

import gateway.resolver as r

# ---------------------------------------------------------------------------
# Stub external deps: Redis + Memory-API HTTP call
# ---------------------------------------------------------------------------

r._redis = SimpleNamespace(get=lambda *_: None, setex=lambda *_: None)


class _DummyResp:  # minimal stand-in for httpx.Response
    status_code = 200

    def json(self):
        return {"id": "foo-bar-2020", "option": "dummy"}


class _DummyClient:
    def __init__(self, *a, **kw): ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        ...

    async def get(self, *_a, **_kw):
        return _DummyResp()


r.httpx.AsyncClient = _DummyClient  # type: ignore

# ---------------------------------------------------------------------------


async def _run():
    result = await r.resolve_decision_text("foo-bar-2020")
    assert result["id"] == "foo-bar-2020"


def test_slug_fast_path():
    asyncio.run(_run())