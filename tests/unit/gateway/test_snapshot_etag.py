import pytest

from gateway.evidence import EvidenceBuilder


class DummyResp:
    """Lightweight httpx.Response stand‑in for snapshot_etag extraction tests."""

    def __init__(self, json_data: dict, headers: dict):
        self._json = json_data
        self.headers = headers
        self.status_code = 200

    def json(self) -> dict:
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class StubClient:
    """
    Minimal httpx.AsyncClient stub that refuses extra kwargs on construction and
    exposes get/post methods.  It always returns a fixed snapshot_etag on the
    anchor enrichment call and no neighbours on expansion.  The absence of an
    __aenter__ hook exercises the fallback path in _safe_async_client, while
    still permitting direct usage for event enrichment.
    """

    def __init__(self):
        pass

    async def get(self, url):
        # Only the first GET call (anchor enrichment) is expected to run;
        # return a snapshot_etag via a hyphenated header variant.
        return DummyResp({"id": "slug-a"}, {"Snapshot-ETag": "etag-a"})

    async def post(self, url, json):
        # expansion stage returns no neighbours and no meta tag
        return DummyResp({"neighbors": [], "meta": {}}, {})

    async def aclose(self):
        # permit aclose to be awaited without side effects
        pass


@pytest.mark.asyncio
async def test_snapshot_etag_extracted(monkeypatch) -> None:
    """
    EvidenceBuilder should propagate the snapshot_etag from the anchor enrichment
    response headers onto the resulting WhyDecisionEvidence instance.  The
    snapshot_etag field must not be serialized by model_dump (spec §H3).
    """
    # Substitute the real AsyncClient with our stub that raises TypeError on
    # unsupported kwargs.  EvidenceBuilder should still extract the header
    # correctly using the _safe_async_client fallback and set snapshot_etag.
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", StubClient)
    eb = EvidenceBuilder(redis_client=None)
    ev = await eb.build("slug-a")
    # The snapshot_etag property reflects the hyphenated header variant
    assert ev.snapshot_etag == "etag-a"
    # model_dump must exclude the snapshot_etag field entirely (spec §H3)
    assert "snapshot_etag" not in ev.model_dump()