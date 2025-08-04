from core_models.models import (WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions,
                            WhyDecisionAnswer, WhyDecisionResponse, CompletenessFlags)
from core_validator import validate_response

def _mk_resp(ev: WhyDecisionEvidence, sup: list[str]):
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=sup)
    return WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={"prompt_id":"p","policy_id":"p"})

def test_missing_transitions_field():
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="D1"), events=[])
    ev.allowed_ids = ["D1"]
    ok, errs = validate_response(_mk_resp(ev, ["D1"]))
    assert ok and not errs

def test_orphan_event():
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="D1"),
                             events=[{"id":"E2"}],
                             transitions=WhyDecisionTransitions())
    ev.allowed_ids = ["D1","E2"]
    ok, errs = validate_response(_mk_resp(ev, ["D1"]))
    assert not ok and "supporting_ids ⊈ allowed_ids" in errs[0]

def test_no_transitions():
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="D1"),
                             events=[],
                             transitions=WhyDecisionTransitions())
    ev.allowed_ids = ["D1"]
    ok, errs = validate_response(_mk_resp(ev, ["D1"]))
    assert ok
