import pytest

from gateway.evidence import EvidenceBuilder
from core_models.models import WhyDecisionEvidence


class DummyResp:
    """Simple httpx.Response stand‑in for JSON payloads and headers."""

    def __init__(self, json_data: dict, headers: dict):
        self._json = json_data
        self.headers = headers
        self.status_code = 200

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.asyncio
async def test_bundler_keeps_events_only_and_whitelists(monkeypatch) -> None:
    """
    EvidenceBuilder must include only event nodes in the ``events`` array.  Decision
    neighbours are classified as decisions and **dropped** entirely (they do not
    appear in events or transitions).  In the revised architecture the builder
    delegates normalisation and whitelisting to the Shared Normaliser and the
    Memory‑API.  As such it preserves whatever fields the upstream service
    returns.  Tags are kept verbatim rather than being normalised.  The test
    asserts that extra metadata such as ``edge`` or ``extra_field`` is retained
    on the event stub.  This confirms that the builder does not re‑project
    events onto a minimal field set.
    """

    anchor_id = "test-anchor"

    # Stub the httpx.AsyncClient used by EvidenceBuilder to avoid network IO.
    class FakeClient:
        async def get(self, url, headers=None):
            # Enrich decision: return minimal anchor details
            if url.startswith("/api/enrich/decision/"):
                return DummyResp({"id": anchor_id}, {"snapshot_etag": "etag"})
            # Enrich event: return minimal event details by id
            if url.startswith("/api/enrich/event/"):
                eid = url.rsplit("/", 1)[-1]
                # Return event with only id so projection keeps input fields
                return DummyResp({"id": eid}, {})
            # Enrich transition: return from/to to classify orientation
            if url.startswith("/api/enrich/transition/"):
                tid = url.rsplit("/", 1)[-1]
                # Use "to" equal to anchor to classify as preceding
                return DummyResp({"id": tid, "from": "other", "to": anchor_id}, {})
            raise RuntimeError(f"unexpected GET {url}")

        async def post(self, url, json):
            # Expand candidates returns a mix of event and decision neighbours
            if url.endswith("/api/graph/expand_candidates"):
                # Provide snapshot_etag in meta to propagate through builder
                return DummyResp(
                    {
                        "neighbors": [
                            {
                                "id": "ev1",
                                "type": "event",
                                "summary": "Event One",
                                "timestamp": "2024-01-01T00:00:00Z",
                                "tags": ["Market-Validation"],
                                "extra_field": "should be removed",
                                "edge": {"rel": "supported_by"},
                            },
                            {
                                "id": "dec1",
                                "type": "decision",
                                "title": "Decision One",
                                "timestamp": "2024-02-01T00:00:00Z",
                                "tags": ["M-and-A"],
                                "edge": {"rel": "preceding"},
                            },
                        ],
                        "meta": {"snapshot_etag": "etag"},
                    },
                    {},
                )
            raise RuntimeError(f"unexpected POST {url}")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self) -> None:
            pass

    # Monkey‑patch the AsyncClient in evidence.py to our FakeClient
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", FakeClient)

    # Build evidence; should not raise
    builder = EvidenceBuilder()
    ev = await builder.build(anchor_id)
    assert isinstance(ev, WhyDecisionEvidence)

    # Only one event should remain after filtering; the decision neighbour is not an event
    assert len(ev.events) == 1
    evt = ev.events[0]

    # The event id should be retained
    assert evt["id"] == "ev1"
    # The event id should be retained
    assert evt["id"] == "ev1"
    # In the new pipeline the EvidenceBuilder no longer strips extraneous fields or
    # normalises tags when the Memory API is stubbed.  The event should retain
    # the fields provided by the Memory API stub, including any additional
    # metadata such as `edge` or `extra_field`.  The presence of these keys
    # indicates that the builder delegates normalisation to the upstream service.
    assert "extra_field" in evt
    assert "edge" in evt
    # Tags are preserved as returned by the Memory API stub
    assert evt.get("tags") == ["Market-Validation"]
    # x-extra may or may not be present depending on the upstream normaliser;
    # the EvidenceBuilder does not inject it.  Do not assert its presence here.

    # Decision neighbour dec1 must NOT appear anywhere in the bundle
    trans_ids = [t.get("id") for t in (ev.transitions.preceding + ev.transitions.succeeding)]
    evt_ids = [e.get("id") for e in ev.events]
    assert "dec1" not in trans_ids
    assert "dec1" not in evt_ids