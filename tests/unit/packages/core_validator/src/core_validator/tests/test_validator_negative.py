from core_validator.validator import validate_response
from gateway.models import WhyDecisionResponse


def test_validator_detects_subset_violation() -> None:
    """Validator should fail when supporting_ids ⊄ allowed_ids."""
    resp = WhyDecisionResponse(
        intent="why_decision",
        evidence={
            "anchor": {"id": "d1", "option": "opt", "rationale": "why"},
            "events": [],
            "transitions": {"preceding": [], "succeeding": []},
            "allowed_ids": ["d1"],
        },
        answer={
            "short_answer": "because",
            "supporting_ids": ["d1", "bad"],
        },
        completeness_flags={
            "event_count": 0,
            "has_preceding": False,
            "has_succeeding": False,
        },
        meta={
            "prompt_id": "pid",
            "policy_id": "polid",
            "prompt_fingerprint": "pf",
            "bundle_fingerprint": "bf",
        },
    )

    valid, errs = validate_response(resp)
    assert not valid
    assert any("supporting_ids" in e for e in errs)
