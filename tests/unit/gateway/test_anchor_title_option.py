import pytest

from gateway.evidence import EvidenceBuilder


class DummyResp:
    """Lightweight stand‑in for an httpx.Response with JSON payload and headers."""

    def __init__(self, json_data: dict, headers: dict | None = None, status_code: int = 200) -> None:
        self._json = json_data
        self.headers = headers or {}
        self.status_code = status_code

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.asyncio
async def test_anchor_title_mirrors_option(monkeypatch) -> None:
    """
    When a decision is enriched by the Memory API without a ``title`` field but with an ``option``
    field, the resulting anchor should expose that option as its ``title``.  The mirroring is now
    handled by the shared normaliser, but the EvidenceBuilder continues to ensure the property is set.
    """
    anchor_id = "decision-A"
    dec_json = {
        "id": anchor_id,
        "option": "Make more widgets",
        "supported_by": [],
        "transitions": [],
    }

    # Stub AsyncClient to return the enriched decision and no neighbours.
    class FakeClient:
        async def get(self, url, headers=None):
            if url == f"/api/enrich/decision/{anchor_id}":
                return DummyResp(dec_json, {"snapshot_etag": "etag1"})
            return DummyResp({}, {})

        async def post(self, url, json):
            # Return empty neighbours to avoid touching event/transition endpoints
            if url.endswith("/api/graph/expand_candidates"):
                return DummyResp({"neighbors": [], "meta": {}}, {})
            return DummyResp({}, {})

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", FakeClient)

    # Provide a dummy Redis client so EvidenceBuilder does not attempt to use a real cache
    class DummyRedis:
        def get(self, *args, **kwargs):
            return None

        def setex(self, *args, **kwargs):
            return None

    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda *_: DummyRedis())

    builder = EvidenceBuilder()
    ev = await builder.build(anchor_id)
    # Assert that the anchor title is mirrored from the option field
    assert ev.anchor.title == dec_json["option"], f"expected anchor.title to mirror option value; got {ev.anchor.title}"