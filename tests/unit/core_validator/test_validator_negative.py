from core_validator import validate_response
from core_models.models import WhyDecisionResponse


def test_validator_detects_subset_violation() -> None:
    """Validator should mark the bundle invalid (trigger fallback) when supporting_ids ⊄ allowed_ids is the only citation issue; it removes out-of-scope IDs and reports a structured error."""
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
            "snapshot_etag": "etag",
            "retries": 0,
            "latency_ms": 123,
            "fallback_used": False,
        },
    )

    valid, errs = validate_response(resp)
    assert valid is False
    codes = {e.get("code") for e in errs if isinstance(e, dict)}
    assert "supporting_ids_removed_invalid" in codes
