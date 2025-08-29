import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app
import gateway.builder as gb

from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence, WhyDecisionTransitions


def _build_evidence(anchor_id: str, n_events: int) -> WhyDecisionEvidence:
    """Helper to construct evidence with a given number of events."""
    anchor = WhyDecisionAnchor(id=anchor_id)
    events = []
    for i in range(n_events):
        # Use ascending timestamps so that the deterministic ranking
        # preserves the order of insertion when patched
        events.append({
            "id": f"{anchor_id}-e{i}",
            "type": "event",
            "timestamp": f"2025-01-{i+1:02d}T00:00:00Z",
            "summary": f"Event {i}",
        })
    # transitions absent for these tests
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
    )
    return evidence


@pytest.mark.asyncio
async def test_events_policy_small(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When three or fewer events are present the response should return all of them.
    The supporting_ids should include the anchor followed by the three events
    in the order ranked by the selector.  ``events_total`` reflects the full
    count and ``events_truncated`` is False.
    """
    anchor_id = "anchor-small"
    evidence = _build_evidence(anchor_id, 3)

    # Stub the evidence builder to return our evidence
    async def _stub_build(_aid: str):  # pragma: no cover
        return evidence
    monkeypatch.setattr(gw_app._evidence_builder, "build", _stub_build, raising=True)

    # Patch the event ranker to preserve the list order
    def _stub_rank_events(_anchor, events):  # pragma: no cover
        return events
    monkeypatch.setattr(gb._selector, "rank_events", _stub_rank_events, raising=True)

    # Stub the LLM client to return a valid JSON answer with only the anchor in supporting_ids
    def _stub_summarise(_env, *args, **kwargs):  # pragma: no cover
        return gb.orjson.dumps({"short_answer": "Because.", "supporting_ids": [anchor_id]}).decode()
    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise, raising=True)

    # Avoid legacy validate_and_fix adjustments
    monkeypatch.setattr(gb.templater, "validate_and_fix", lambda a, l, anch: (a, False, []), raising=True)

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": anchor_id})
    body = resp.json()
    evs = body["evidence"]["events"]
    assert len(evs) == 3, "all events should be returned when ≤3"
    # supporting_ids should include anchor followed by the three events
    supp = body["answer"]["supporting_ids"]
    assert supp == [anchor_id] + [f"{anchor_id}-e{i}" for i in range(3)], "supporting_ids should include the anchor and the three events in order"
    meta = body.get("meta", {})
    assert meta.get("events_total") == 3, "events_total should reflect the full count"
    assert meta.get("events_truncated") is False or meta.get("events_truncated") is None, "events_truncated should be False when nothing is truncated"


@pytest.mark.asyncio
async def test_events_policy_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When more than ten events are present the response should return only the top ten
    events.  The supporting_ids should include the anchor followed by the top
    three events.  ``events_total`` reflects the full count and
    ``events_truncated`` is True.
    """
    anchor_id = "anchor-large"
    evidence = _build_evidence(anchor_id, 12)

    async def _stub_build(_aid: str):  # pragma: no cover
        return evidence
    monkeypatch.setattr(gw_app._evidence_builder, "build", _stub_build, raising=True)

    # Patch the ranker to preserve list order
    def _stub_rank_events(_anchor, events):  # pragma: no cover
        return events
    monkeypatch.setattr(gb._selector, "rank_events", _stub_rank_events, raising=True)

    def _stub_summarise(_env, *args, **kwargs):  # pragma: no cover
        return gb.orjson.dumps({"short_answer": "Because.", "supporting_ids": [anchor_id]}).decode()
    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise, raising=True)

    monkeypatch.setattr(gb.templater, "validate_and_fix", lambda a, l, anch: (a, False, []), raising=True)

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": anchor_id})
    body = resp.json()
    evs = body["evidence"]["events"]
    assert len(evs) == 10, "only the top ten events should be returned"
    # supporting_ids includes anchor + first three events
    supp = body["answer"]["supporting_ids"]
    assert supp == [anchor_id] + [f"{anchor_id}-e{i}" for i in range(3)], "supporting_ids should include the anchor and the top three events"
    meta = body.get("meta", {})
    assert meta.get("events_total") == 12, "events_total should reflect the full count"
    assert meta.get("events_truncated") is True, "events_truncated should be True when truncation occurs"
