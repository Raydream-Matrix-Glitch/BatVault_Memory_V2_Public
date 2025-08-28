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
    # The validator returns True on permissive paths.  For a minimal
    # evidence bundle containing only the anchor the validator may perform
    # schema hygiene repairs but should not emit business‑rule violations.
    assert ok is True
    codes = {e.get("code") for e in errs if isinstance(e, dict)}
    assert "supporting_ids_missing_transition" not in codes
    assert "completeness_event_count_mismatch" not in codes

def test_orphan_event():
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="D1"),
                             events=[{"id":"E2"}],
                             transitions=WhyDecisionTransitions())
    ev.allowed_ids = ["D1","E2"]
    ok, errs = validate_response(_mk_resp(ev, ["D1"]))
    assert ok is True
    assert errs, "Expected validation errors for orphan event"
    codes = {e.get("code") for e in errs if isinstance(e, dict)}
    assert "completeness_event_count_mismatch" in codes

def test_no_transitions():
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="D1"),
                             events=[],
                             transitions=WhyDecisionTransitions())
    ev.allowed_ids = ["D1"]
    ok, errs = validate_response(_mk_resp(ev, ["D1"]))
    assert ok is True
    codes = {e.get("code") for e in errs if isinstance(e, dict)}
    assert "supporting_ids_missing_transition" not in codes
    assert "completeness_event_count_mismatch" not in codes
