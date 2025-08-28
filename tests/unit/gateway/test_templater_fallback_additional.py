import pytest

from gateway.templater import finalise_short_answer
from core_models.models import (
    WhyDecisionEvidence,
    WhyDecisionAnchor,
    WhyDecisionTransitions,
    WhyDecisionAnswer,
)


def test_finalise_short_answer_empty_uses_rationale() -> None:
    """
    finalise_short_answer must synthesise a deterministic fallback when the
    incoming answer has an empty short_answer.  The fallback should derive
    from the anchor rationale and include the most recent event summary when
    available, and it must never contain the substring "STUB ANSWER".  It
    should not exceed 320 characters.
    """
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
    ans = WhyDecisionAnswer(short_answer="", supporting_ids=["A1"])
    fixed, changed = finalise_short_answer(ans, evidence)
    assert changed is True, "finalise_short_answer must indicate a change when fallback is applied"
    assert fixed.short_answer.startswith("Because of reasons"), "fallback should begin with the anchor rationale"
    assert "STUB ANSWER" not in fixed.short_answer, "fallback must not contain stub markers"
    assert len(fixed.short_answer) <= 320, "fallback must be truncated to the 320 character limit"