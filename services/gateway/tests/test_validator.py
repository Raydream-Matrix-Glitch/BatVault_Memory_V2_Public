from gateway.models import WhyDecisionResponse, WhyDecisionAnswer, WhyDecisionAnchor, WhyDecisionEvidence, WhyDecisionTransitions, CompletenessFlags
from core_validator import validate_response

import json, tarfile, pathlib

SNAPSHOT = next((path for path in (pathlib.Path(__file__).parent / "fixtures").glob("*snapshot*.tar.gz")), None)
assert SNAPSHOT and SNAPSHOT.exists(), "snapshot fixture missing (B-6)"

def _anchor_id_from_snapshot() -> str:
    with tarfile.open(SNAPSHOT) as t:
        first_decision = next(f for f in t.getnames() if f.endswith(".json") and "/decisions/" in f)
        return pathlib.Path(first_decision).stem


def test_validator_subset_rule():

    anchor_id = _anchor_id_from_snapshot()
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id=anchor_id),
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=[anchor_id, "E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=["A1"])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert ok
    assert not errs

def test_validator_missing_anchor():
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id="D-X"),
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=["E1"])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert not ok
    assert "anchor.id missing" in errs[0]
