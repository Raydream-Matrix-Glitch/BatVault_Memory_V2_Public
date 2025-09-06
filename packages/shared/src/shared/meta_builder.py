from typing import Any, Dict
from core_logging import get_logger, log_stage
from core_models.meta_inputs import MetaInputs
from core_models.models import MetaInfo

_logger = get_logger("shared.meta_builder")

def build_meta(meta: MetaInputs, request_id: str) -> MetaInfo:
    """
    Deterministic assembly of the canonical MetaInfo (new nested schema).
    """
    # One concise audit log for the drawer
    try:
        log_stage(
            "meta", "summary",
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
            pool_total=meta.evidence_counts.pool.total if hasattr(meta.evidence_counts.pool, "total") else None,
            prompt_total=meta.evidence_counts.prompt_included.total if hasattr(meta.evidence_counts.prompt_included, "total") else None,
            payload_total=meta.evidence_counts.payload_serialized.total if hasattr(meta.evidence_counts.payload_serialized, "total") else None,
            prompt_truncation=meta.truncation_metrics.prompt_truncation,
        )
    except Exception:
        pass

    # Pydantic will validate and forbid extras at this boundary.
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
        "validator": meta.validator.model_dump(),
        "load_shed": meta.load_shed,
        "policy_trace": meta.policy_trace,
    }
    return MetaInfo(**payload)