
import re
import orjson
import pytest
from fastapi.testclient import TestClient

import gateway.builder as gb
from gateway.app import app
import gateway.templater as templater
from core_models.models import (
    WhyDecisionEvidence,
    WhyDecisionAnchor,
    WhyDecisionTransitions,
)


def test_compose_fallback_answer_maker_date_events() -> None:
    """
    Fallback answer should include maker/date and a ``Next:`` pointer when a succeeding transition exists.
    It must not leak raw IDs or append a ``Key events:`` tail and must remain within the 320‑character limit.
    """
    anchor = WhyDecisionAnchor(
        id="philips-exit",
        decision_maker="Frans van Houten",
        timestamp="2011-04-18T00:00:00Z",
        rationale=(
            "losses and margin pressure in TVs made the business unattractive; "
            "the company redirected focus to higher‑margin categories."
        ),
    )
    events = [
        {
            "id": "phil-e1",
            "type": "event",
            "timestamp": "2010-12-31T00:00:00Z",
            "summary": "Loss due to tv business slump",
        },
        {
            "id": "phil-e2",
            "type": "event",
            "timestamp": "2011-03-01T00:00:00Z",
            "summary": "Margin pressure due to competition",
        },
    ]
    transitions = WhyDecisionTransitions(
        preceding=[],
        succeeding=[{"id": "trans-phil-2011-2013", "to": "philips-led-lighting-focus-2013", "title": "shift toward growth areas like LED lighting"}],
    )
    ev = WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        transitions=transitions,
        allowed_ids=["philips-exit", "phil-e1", "phil-e2", "trans-phil-2011-2013"],
    )
    ans = templater._compose_fallback_answer(ev)
    assert len(ans) <= 320
    sentences = [s for s in re.split(r"[.!?]", ans) if s.strip()]
    # Fallback must be at most two sentences
    assert len(sentences) <= 2
    # A succeeding transition implies a Next pointer
    assert "Next:" in ans
    # Raw IDs must not appear in the fallback answer
    assert "philips-led-lighting-focus-2013" not in ans
    # The fallback must not include the "Key events" tail
    assert "Key events" not in ans
    # Avoid double punctuation
    assert ".." not in ans


def test_clamp_long_answer_triggers_style_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM answers exceeding 320 characters must trigger style_violation fallback."""
    anchor = WhyDecisionAnchor(
        id="anchor-long",
        decision_maker="Alice",
        timestamp="2025-05-20T00:00:00Z",
        rationale="Because reasons.",
    )
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["anchor-long"],
    )
    from gateway.app import _evidence_builder
    async def _build_stub(_aid: str):
        return evidence
    monkeypatch.setattr(_evidence_builder, "build", _build_stub, raising=True)
    long_text = "A" * 400
    def _stub_summarise_json(_env, *args, **kwargs):
        return orjson.dumps({"short_answer": long_text, "supporting_ids": ["anchor-long"]}).decode()
    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise_json, raising=True)
    monkeypatch.setattr(gb.templater, "validate_and_fix", lambda a, l, anch: (a, False, []), raising=True)
    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "anchor-long"})
    body = resp.json()
    meta = body.get("meta", {})
    assert meta.get("fallback_used") is True
    assert meta.get("fallback_reason") == "style_violation"
    short = body.get("answer", {}).get("short_answer", "")
    assert short and len(short) <= 320


def test_id_scrubbing_removes_allowed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw allowed IDs emitted by the LLM must be scrubbed from the short answer."""
    anchor = WhyDecisionAnchor(id="anchor-scrub", rationale="A reason.")
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["anchor-scrub", "ev1"],
    )
    from gateway.app import _evidence_builder
    async def _stub_build(_aid: str):
        return evidence
    monkeypatch.setattr(_evidence_builder, "build", _stub_build, raising=True)
    def _stub_summarise(_env, *args, **kwargs):
        return orjson.dumps({"short_answer": "This happened because of ev1 causing problems.", "supporting_ids": ["anchor-scrub"]}).decode()
    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise, raising=True)
    monkeypatch.setattr(gb.templater, "validate_and_fix", lambda a, l, anch: (a, False, []), raising=True)
    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "anchor-scrub"})
    assert "ev1" not in resp.json().get("answer", {}).get("short_answer", "")


def test_event_dedup_and_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate events on the same day are collapsed and amounts are normalised."""
    anchor = WhyDecisionAnchor(
        id="dedup-anchor",
        decision_maker="Dana",
        timestamp="2024-01-01T00:00:00Z",
        rationale="Because of large losses.",
    )
    events = [
        {
            "id": "ev-usd",
            "type": "event",
            "timestamp": "2023-12-31T00:00:00Z",
            "summary": "$913 m operating loss in plasma division",
        },
        {
            "id": "ev-jpy",
            "type": "event",
            "timestamp": "2023-12-31T12:00:00Z",
            "summary": "¥913 m operating loss in plasma division",
        },
    ]
    transitions = WhyDecisionTransitions(preceding=[{"id": "tr1"}], succeeding=[])
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        transitions=transitions,
        allowed_ids=["dedup-anchor", "ev-usd", "ev-jpy", "tr1"],
    )
    from gateway.app import _evidence_builder
    async def _stub_build(_aid: str):
        return evidence
    monkeypatch.setattr(_evidence_builder, "build", _stub_build, raising=True)
    def _stub_summarise(_env, *args, **kwargs):
        return orjson.dumps({"short_answer": "Due to losses.", "supporting_ids": ["dedup-anchor"]}).decode()
    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise, raising=True)
    monkeypatch.setattr(gb.templater, "validate_and_fix", lambda a, l, anch: (a, False, []), raising=True)
    client = TestClient(app)
    body = client.post("/v2/ask", json={"anchor_id": "dedup-anchor"}).json()
    evs = body.get("evidence", {}).get("events", [])
    assert len(evs) == 1
    ev0 = evs[0]
    assert "normalized_amount" in ev0
    assert "currency" in ev0
    supp_ids = body.get("answer", {}).get("supporting_ids", [])
    assert not ("ev-usd" in supp_ids and "ev-jpy" in supp_ids)
    assert "dedup-anchor" in supp_ids
    assert "tr1" in supp_ids