import pytest

from gateway.evidence import EvidenceBuilder


class DummyResp:
    """Minimal response stub carrying JSON and headers."""
    def __init__(self, json_data: dict, headers: dict):
        self._json = json_data
        self.headers = headers
        self.status_code = 200
    def json(self) -> dict:
        return self._json
    def raise_for_status(self) -> None:
        pass


@pytest.mark.asyncio
async def test_fallback_isolated_per_builder(monkeypatch) -> None:
    """
    Each EvidenceBuilder instance must start with a clean fallback client.

    When using a stateful stub (e.g. with an internal counter), the counter
    should reset between builders.  Otherwise later tests could observe
    unexpected ETag values.
    """
    # Alternate two different snapshot_etag values on successive calls
    etags = ["etag1", "etag2"]

    class MockClient:
        def __init__(self):
            self._idx = 0
        async def get(self, url):
            etag = etags[self._idx]
            self._idx += 1
            return DummyResp({"id": url.rsplit("/", 1)[-1]}, {"snapshot_etag": etag})
        async def post(self, url, json):
            return DummyResp({"neighbors": [], "meta": {}}, {})
        async def aclose(self):
            pass

    # Patch the AsyncClient used by gateway.evidence to our stub
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", MockClient)

    # First builder sees the first ETag
    b1 = EvidenceBuilder(redis_client=None)
    ev1 = await b1.build("anchor-x")
    assert ev1.snapshot_etag == "etag1"

    # Second builder should reset the stub state and also see the first ETag
    b2 = EvidenceBuilder(redis_client=None)
    ev2 = await b2.build("anchor-x")
    assert ev2.snapshot_etag == "etag1"