from typing import Any, Dict
from core_logging import get_logger, log_stage
from core_models.meta_inputs import MetaInputs

_logger = get_logger("core_models.meta_builder")

def build_meta(meta: MetaInputs, request_id: str) -> dict:
    """Assemble the canonical :class:`MetaInfo` from :class:`MetaInputs`.

    Pure and deterministic: no clocks or I/O; logging uses only provided inputs.
    """
    # One concise audit log for the drawer (deterministic fields only).
    log_stage(
        _logger, "meta", "summary",
        request_id=request_id,
        policy_id=meta.policy.policy_id,
        prompt_id=meta.policy.prompt_id,
        prompt_fp=meta.fingerprints.prompt_fp,
        bundle_fp=meta.fingerprints.bundle_fp,
        snapshot_etag=meta.fingerprints.snapshot_etag,
        llm_mode=meta.policy.llm.mode,
        retries=meta.runtime.retries,
        fallback_used=meta.runtime.fallback_used,
        fallback_reason=meta.runtime.fallback_reason,
        pool_total=getattr(getattr(meta.evidence_counts, "pool", object()), "total", None),
        prompt_total=getattr(getattr(meta.evidence_counts, "prompt_included", object()), "total", None),
        payload_total=getattr(getattr(meta.evidence_counts, "payload_serialized", object()), "total", None),
        prompt_truncation=meta.truncation_metrics.prompt_truncation,
    )

    payload: Dict[str, Any] = {
        "request": meta.request.model_dump(),
        "policy": meta.policy.model_dump(),
        "budgets": meta.budgets.model_dump(),
        "fingerprints": meta.fingerprints.model_dump(),
        "evidence_counts": meta.evidence_counts.model_dump(),
        "evidence_sets": meta.evidence_sets.model_dump(),
        "selection_metrics": meta.selection_metrics.model_dump(),
        "truncation_metrics": meta.truncation_metrics.model_dump(),
        "runtime": meta.runtime.model_dump(),
        "load_shed": meta.load_shed,
        "policy_trace": meta.policy_trace,
    }
    return payload

