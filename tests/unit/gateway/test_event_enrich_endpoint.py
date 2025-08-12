import pytest

from gateway.evidence import EvidenceBuilder


class DummyResp:
    """Lightweight response with JSON payload and headers."""

    def __init__(self, json_data: dict, headers: dict):
        self._json = json_data
        self.headers = headers
        self.status_code = 200

    def json(self) -> dict:
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.asyncio
async def test_enrich_decision_neighbors_use_decision_endpoint(monkeypatch) -> None:
    """
    EvidenceBuilder should call the decision enrichment endpoint when the
    neighbour has type ``decision``.  Previously all neighbours were enriched via
    the event endpoint, causing 404 errors for decision IDs.
    """

    anchor_id = "anchor-x"
    decision_id = "dec-y"
    calls = []

    class FakeClient:
        """httpx.AsyncClient stub that records GET URLs and returns fixtures."""

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self) -> None:
            pass

        async def get(self, url, headers=None):  # type: ignore[override]
            calls.append(url)
            if url.startswith(f"/api/enrich/decision/{anchor_id}"):
                return DummyResp({"id": anchor_id}, {"snapshot_etag": "etag1"})
            if url.startswith(f"/api/enrich/decision/{decision_id}"):
                return DummyResp({"id": decision_id}, {})
            # Event endpoint should not be invoked for decision neighbours
            if url.startswith(f"/api/enrich/event/{decision_id}"):
                return DummyResp({"id": decision_id}, {})
            return DummyResp({}, {})

        async def post(self, url, json):  # type: ignore[override]
            # Return a single neighbour of type "decision"
            return DummyResp(
                {
                    "neighbors": [
                        {
                            "id": decision_id,
                            "type": "decision",
                            "edge": {"rel": None},
                        }
                    ],
                    "meta": {},
                },
                {},
            )

    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", FakeClient)

    builder = EvidenceBuilder(redis_client=None)
    ev = await builder.build(anchor_id)

    # Decision neighbours must NOT enter events or allowed_ids
    assert all(item.get("type") == "event" for item in ev.events), "non-event leaked into events"
    assert all(item.get("id") != decision_id for item in ev.events), "decision present in events"
    assert decision_id not in (ev.allowed_ids or []), "decision present in allowed_ids"

    # And we must NOT call the event enrichment endpoint for a decision id
    assert not any(u.startswith(f"/api/enrich/event/{decision_id}") for u in calls)

    # Decision neighbours are recognised and DROPPED from the build context
    assert all(item.get("id") != decision_id for item in ev.events), "decision leaked into events"
    # And no enrichment calls should be made for a dropped decision id
    assert not any(
        path.endswith(f"/{decision_id}") for path in calls
    ), f"Unexpected enrichment call for dropped decision: {calls}"