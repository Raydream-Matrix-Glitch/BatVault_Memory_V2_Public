import pytest

from gateway.templater import finalise_short_answer
from core_models.models import (
    WhyDecisionEvidence,
    WhyDecisionAnchor,
    WhyDecisionTransitions,
    WhyDecisionAnswer,
)


def test_finalise_short_answer_replaces_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    finalise_short_answer must replace stub answers with a deterministic fallback
    derived from the evidence.  The fallback uses the anchor's rationale
    when available and should never contain the phrase ``STUB ANSWER``.
    """
    # Build evidence with rationale and a single event
    anchor = WhyDecisionAnchor(id="A1", rationale="Because of reasons.")
    events = [
        {
            "id": "E1",
            "type": "event",
            "timestamp": "2025-01-02T00:00:00Z",
            "summary": "An important milestone",
        }
    ]
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
        allowed_ids=["A1", "E1"],
    )
    ans = WhyDecisionAnswer(short_answer="STUB ANSWER: placeholder", supporting_ids=["A1"])
    fixed, changed = finalise_short_answer(ans, evidence)
    assert changed is True
    assert "STUB ANSWER" not in fixed.short_answer
    # Fallback should start with the rationale
    assert fixed.short_answer.startswith("Because of reasons")
    # Length must not exceed 320 characters
    assert len(fixed.short_answer) <= 320


def test_finalise_short_answer_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    finalise_short_answer must leave non-stub answers unchanged except for
    enforcing the maximum length limit.
    """
    anchor = WhyDecisionAnchor(id="A1", rationale="Reason")
    evidence = WhyDecisionEvidence(
        anchor=anchor,
        events=[],
        transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
        allowed_ids=["A1"],
    )
    # Already valid answer
    ans = WhyDecisionAnswer(short_answer="This is a valid answer.", supporting_ids=["A1"])
    fixed, changed = finalise_short_answer(ans, evidence)
    assert changed is False
    assert fixed.short_answer == "This is a valid answer."