from __future__ import annotations
from typing import List, Tuple
from gateway.models import WhyDecisionResponse

__all__ = ["validate_response"]


def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
    """Validate full WhyDecisionResponse and return (is_valid, errors)."""
    errs: List[str] = []

    # -------- schema validation ---------------------------------- #
    try:
        WhyDecisionResponse.model_validate(resp.model_dump(mode="python"))
    except Exception as exc:
        errs.append(f"response schema error: {exc}")

    # Required meta fields
    for key in ("prompt_id", "policy_id", "prompt_fingerprint", "bundle_fingerprint"):
        if not resp.meta.get(key):
            errs.append(f"meta.{key} missing")

    # supporting_ids ⊆ allowed_ids
    allowed = set(resp.evidence.allowed_ids)
    support = set(resp.answer.supporting_ids)
    if not support.issubset(allowed):
        errs.append("supporting_ids ⊈ allowed_ids")

    # anchor cited
    anchor_id = resp.evidence.anchor.id
    if anchor_id and anchor_id not in support:
        errs.append("anchor.id missing from supporting_ids")

    # transitions cited
    trans_ids = [
        t.get("id")
        for t in resp.evidence.transitions.preceding + resp.evidence.transitions.succeeding
        if t.get("id")
    ]
    if trans_ids and not set(trans_ids).issubset(support):
        errs.append("transition ids must be cited in supporting_ids")

    # completeness flags
    cf = resp.completeness_flags
    if cf.event_count != len(resp.evidence.events):
        errs.append("completeness_flags.event_count mismatch")
    if cf.has_preceding != bool(resp.evidence.transitions.preceding):
        errs.append("completeness_flags.has_preceding mismatch")
    if cf.has_succeeding != bool(resp.evidence.transitions.succeeding):
        errs.append("completeness_flags.has_succeeding mismatch")

    return (not errs), errs
