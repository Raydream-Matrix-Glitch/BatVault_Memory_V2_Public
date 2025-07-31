from __future__ import annotations
from typing import List, Tuple
from gateway.models import WhyDecisionResponse
from gateway.models import WhyDecisionAnswer  # generated Pydantic model

__all__ = ["validate_response"]


def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
    errs: List[str] = []

    # -------- schema validation ---------------------------------- #
    try:
        WhyDecisionAnswer.model_validate(resp.answer.model_dump(mode="python"))
    except Exception as exc:
        errs.append(f"answer schema error: {exc}")

    # prompt_id / policy_id must be present (patch 5)
    if not resp.meta.get("prompt_id"):
        errs.append("meta.prompt_id missing")
    if not resp.meta.get("policy_id"):
        errs.append("meta.policy_id missing")

    allowed = set(resp.evidence.allowed_ids)
    support = set(resp.answer.supporting_ids)

    if not support.issubset(allowed):
        errs.append("supporting_ids ⊈ allowed_ids")

    anchor_id = resp.evidence.anchor.id
    if anchor_id and anchor_id not in support:
        errs.append("anchor.id missing from supporting_ids")

    trans_ids = [
        t.get("id")
        for t in resp.evidence.transitions.preceding + resp.evidence.transitions.succeeding
        if t.get("id")
    ]
    if trans_ids and not set(trans_ids).issubset(support):
        errs.append("transition ids must be cited in supporting_ids")

    return (not errs), errs
