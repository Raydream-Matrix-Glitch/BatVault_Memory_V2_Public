import pytest, json
pytest.importorskip("pytest_asyncio")  # clean “SKIPPED – requires pytest-asyncio”
from httpx import Response
from gateway.evidence import EvidenceBuilder

class _DummyCM:
    """Mimics httpx.AsyncClient for monkey-patching."""
    def __init__(self): self.last_json = None
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, url, json=None, **kw):
        self.last_json = json
        return Response(200, json={"neighbors":[]})
    async def get(self, url, **kw):              # enrich stub
        return Response(200, json={"id": "dummy"})

@pytest.mark.asyncio
async def test_expand_candidates_uses_node_id(monkeypatch):
    dummy = _DummyCM()
    monkeypatch.setattr("gateway.evidence._safe_async_client",
                        lambda **kw: dummy)
    eb = EvidenceBuilder(redis_client=None)
    await eb.get_evidence("panasonic-exit-plasma-2012")
    assert dummy.last_json == {"node_id": "panasonic-exit-plasma-2012", "k": 1}