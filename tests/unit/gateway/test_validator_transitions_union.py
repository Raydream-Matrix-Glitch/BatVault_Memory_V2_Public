import pytest
from core_models.models import (
    WhyDecisionResponse,
    WhyDecisionAnswer,
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_validator import validate_response

def test_validator_union_includes_transitions():
    anchor_id = "A"
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id=anchor_id),
        events=[{"id": "E1", "type": "event", "timestamp": "2025-01-01T00:00:00Z"}],
        transitions=WhyDecisionTransitions(
            preceding=[{"id": "Tpre", "from": "X", "to": anchor_id}],
            succeeding=[{"id": "Tsuc", "from": anchor_id, "to": "Y"}],
        ),
        # validator enforces exact union; this is already correct
        allowed_ids=[anchor_id, "E1", "Tpre", "Tsuc"],
    )
    ans = WhyDecisionAnswer(short_answer="ok", supporting_ids=[anchor_id, "Tpre", "Tsuc"])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert ok is True
    assert errs == []
