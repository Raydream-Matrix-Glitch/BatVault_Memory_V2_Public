from core_models.models import (
    WhyDecisionResponse,
    WhyDecisionAnswer,
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_validator import validate_response
import json
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
    anchor_id = _anchor_id_from_fixtures()
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id=anchor_id),
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=[anchor_id, "E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=[anchor_id])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert ok
    assert not errs

def test_validator_missing_anchor():
    anchor_id = _anchor_id_from_fixtures()
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id="D-X"),
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=[anchor_id])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert not ok
    assert "anchor.id missing" in errs[0]
