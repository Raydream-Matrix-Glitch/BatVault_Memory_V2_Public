from __future__ import annotations
from typing import List, Tuple
from core_models.models import WhyDecisionResponse

__all__ = ["validate_response"]


def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
    """Validate full WhyDecisionResponse and return (is_valid, errors)."""
    errs: List[str] = []

    # --- WhyDecisionAnswer@1 length limits (spec §F1) -------------------- #
    if len(resp.answer.short_answer or "") > 320:
        errs.append("short_answer exceeds 320 characters")
    if resp.answer.rationale_note and len(resp.answer.rationale_note) > 280:
        errs.append("rationale_note exceeds 280 characters")

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

    # --- allowed_ids exact union (spec §B2) ------------------------------ #
    expected = {resp.evidence.anchor.id}
    expected |= {e.get("id") for e in resp.evidence.events
                 if isinstance(e, dict) and e.get("id")}
    expected |= {t.get("id") for t in (resp.evidence.transitions.preceding +
                                       resp.evidence.transitions.succeeding)
                 if isinstance(t, dict) and t.get("id")}
    if set(resp.evidence.allowed_ids) != expected:
        errs.append("allowed_ids mismatch union of anchor, events and transitions")

    # completeness flags
    cf = resp.completeness_flags
    if cf.event_count != len(resp.evidence.events):
        errs.append("completeness_flags.event_count mismatch")
    if cf.has_preceding != bool(resp.evidence.transitions.preceding):
        errs.append("completeness_flags.has_preceding mismatch")
    if cf.has_succeeding != bool(resp.evidence.transitions.succeeding):
        errs.append("completeness_flags.has_succeeding mismatch")

    return (not errs), errs
