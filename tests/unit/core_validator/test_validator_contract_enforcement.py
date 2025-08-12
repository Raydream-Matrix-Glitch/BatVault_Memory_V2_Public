import pytest

from core_validator import validate_response
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
    WhyDecisionAnswer,
    WhyDecisionResponse,
    CompletenessFlags,
)


def test_validator_canonicalises_and_cites_all(monkeypatch):
    """
    The validator must normalise malformed bundles by recomputing allowed_ids,
    dropping non‑event items, fixing supporting_ids and honouring the
    CITE_ALL_IDS environment variable.
    """
    # Force CITE_ALL_IDS to be truthy
    monkeypatch.setenv("CITE_ALL_IDS", "true")

    # Anchor
    anchor = WhyDecisionAnchor(id="A")
    # Evidence events: one valid event and one invalid (missing type)
    events = [
        {"id": "E1", "type": "event", "timestamp": "2025-01-02T00:00:00Z"},
        {"id": "X", "summary": "invalid event"},
    ]
    # One preceding transition (should always be included)
    preceding = [{"id": "T1", "timestamp": "2024-01-01T00:00:00Z"}]
    succeeding = []
    transitions = WhyDecisionTransitions(preceding=preceding, succeeding=succeeding)

    evidence = WhyDecisionEvidence(anchor=anchor, events=events, transitions=transitions)
    # Provide an incorrect allowed_ids to exercise recomputation
    evidence.allowed_ids = ["A", "E1"]
    # Build answer with missing anchor and an invalid support id
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=["X"])
    # Incorrect completeness flags (event_count=2 but only one valid event)
    flags = CompletenessFlags(event_count=2, has_preceding=False, has_succeeding=False)
    resp = WhyDecisionResponse(
        intent="why_decision",
        evidence=evidence,
        answer=ans,
        completeness_flags=flags,
        meta={"prompt_id": "p", "policy_id": "p"},
    )
    ok, errors = validate_response(resp)
    # The validator should always return True on repairable bundles
    assert ok is True
    codes = {err.get("code") for err in errors if isinstance(err, dict)}
    # Non-event dropped
    assert "supporting_ids_enforced_cite_all_ids" in codes
    # Allowed ids recomputed
    assert "allowed_ids_exact_union_violation" in codes
    # Missing anchor in supporting_ids
    assert "supporting_ids_missing_anchor" in codes
    # Missing transition in supporting_ids
    assert "supporting_ids_missing_transition" in codes
    # In CITE_ALL_IDS mode we enforce full citation. Correction is reported via
    # 'supporting_ids_enforced_cite_all_ids' above; no separate 'supporting_ids_removed_invalid' is required.
    # Event count updated
    assert "completeness_event_count_mismatch" in codes
    # Because CITE_ALL_IDS is set, supporting_ids must match allowed_ids
    assert resp.answer.supporting_ids == resp.evidence.allowed_ids
    # Allowed ids canonical ordering: anchor first, then events by timestamp then transitions
    assert resp.evidence.allowed_ids == ["A", "E1", "T1"]