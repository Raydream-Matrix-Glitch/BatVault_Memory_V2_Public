from core_models.models import (
    WhyDecisionResponse,
    WhyDecisionAnswer,
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_validator import validate_response
from pathlib import Path


def _fixture_decisions() -> Path:
    """Return <repo-root>/memory/fixtures/decisions regardless of call-site depth."""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures" / "decisions"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures/decisions directory not found")


MEM_FIXTURES = _fixture_decisions()
assert MEM_FIXTURES.is_dir(), f"memory fixtures not found at {MEM_FIXTURES}"


def _anchor_id_from_fixtures() -> str:
    # Pick the first decision JSON file in memory fixtures
    fn = next(MEM_FIXTURES.glob("*.json"))
    return fn.stem


def test_validator_subset_rule():
    """
    allowed_ids must exactly equal {anchor.id} ∪ {event.id for event in events}.
    We include the event “E1” in both the evidence and allowed_ids so the subset/
    equality rule is genuinely satisfied.
    """
    anchor_id = _anchor_id_from_fixtures()
    # Provide a well‑formed event with type and timestamp so it is not
    # discarded by the validator.  Without these fields the validator
    # would drop the event and emit errors.
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id=anchor_id),
        events=[{"id": "E1", "type": "event", "timestamp": "2025-01-01T00:00:00Z"}],
        transitions=WhyDecisionTransitions(),
        allowed_ids=[anchor_id, "E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=[anchor_id])
    resp = WhyDecisionResponse(
        intent="why_decision",
        evidence=ev,
        answer=ans,
        completeness_flags=CompletenessFlags(event_count=1),
        meta={},
    )
    ok, errs = validate_response(resp)
    # All invariants satisfied → no errors emitted
    assert ok is True
    assert errs == []


def test_validator_missing_anchor():
    """
    When anchor.id is absent from supporting_ids the validator should report
    “anchor.id missing” first.
    """
    anchor_id = _anchor_id_from_fixtures()
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id="D-X"),       # anchor differs from support
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=[anchor_id])
    resp = WhyDecisionResponse(
        intent="why_decision",
        evidence=ev,
        answer=ans,
        completeness_flags=CompletenessFlags(),
        meta={},
    )
    ok, errs = validate_response(resp)
    # The validator is permissive: it returns True but records that the
    # supporting_ids were missing the anchor.  Expect a structured
    # error with the corresponding code.
    assert ok is True
    assert errs, "Expected validation errors for missing anchor id"
    codes = {err.get("code") for err in errs if isinstance(err, dict)}
    assert "supporting_ids_missing_anchor" in codes
