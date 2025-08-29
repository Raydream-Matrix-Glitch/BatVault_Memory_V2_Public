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
    incoming answer has an empty ``short_answer``.  Per the milestone‑5
    contract, the fallback should derive from the anchor rationale and
    **must not** append event summaries or a ``Key events:`` tail.  It must
    never contain the substring ``STUB ANSWER`` and should respect the
    320‑character length limit.  A ``Next:`` pointer is added only when a
    succeeding transition exists (not applicable in this test).
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
    # The empty short_answer should trigger a fallback
    assert changed is True, "finalise_short_answer must indicate a change when fallback is applied"
    # Fallback should begin with the anchor rationale
    assert fixed.short_answer.startswith("Because of reasons"), "fallback should begin with the anchor rationale"
    # Fallback must not contain stub markers or event summaries
    assert "STUB ANSWER" not in fixed.short_answer, "fallback must not contain stub markers"
    assert "An important milestone" not in fixed.short_answer, "fallback must not include event summaries"
    # Fallback must respect the 320 character limit
    assert len(fixed.short_answer) <= 320, "fallback must be truncated to the 320 character limit"