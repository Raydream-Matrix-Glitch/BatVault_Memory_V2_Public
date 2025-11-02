from typing import Any, Dict
from core_logging import get_logger, log_stage
from core_models_gen.models_meta_inputs import MetaInputs

_logger = get_logger("core_models.meta_builder")

def _maybe_get(obj: Any, *path: str, default: Any = None) -> Any:
    """
    Safely fetch a nested attribute or dict key chain.
    Works with either Pydantic models (attr access) or plain dicts (key access).
    Returns `default` (None) if any hop is missing.
    """
    cur = obj
    for key in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            if key not in cur:
                return default
            cur = cur[key]
        else:
            if not hasattr(cur, key):
                return default
            cur = getattr(cur, key)
    return cur

def _asdict(x: Any) -> Dict[str, Any]:
    """Return a JSON-serializable dict from either a Pydantic model or a dict."""
    if x is None:
        return {}
    return x if isinstance(x, dict) else x.model_dump()

def build_meta(meta: MetaInputs, request_id: str) -> dict:
    """Assemble the canonical :class:`MetaInfo` from :class:`MetaInputs`.

    Pure and deterministic: no clocks or I/O; logging uses only provided inputs.
    """
    # One concise audit log for the drawer (deterministic fields only).
    log_stage(
        _logger, "meta", "summary",
        request_id=request_id,
        policy_id=meta.policy.policy_id,
        prompt_id=_maybe_get(meta.policy, "prompt_id"),
        prompt_fp=_maybe_get(meta.fingerprints, "prompt_fp"),
        bundle_fp=_maybe_get(meta.fingerprints, "bundle_fp"),
        snapshot_etag=_maybe_get(meta.fingerprints, "snapshot_etag"),
        # Optional: policy.llm may be absent in LLM-free runs
        llm_mode=_maybe_get(meta.policy, "llm", "mode"),
        # runtime may be a dict; tolerate either dict or model
        retries=_maybe_get(meta.runtime, "retries"),
        fallback_used=_maybe_get(meta.runtime, "fallback_used"),
        fallback_reason=_maybe_get(meta.runtime, "fallback_reason"),
        # evidence_counts / truncation_metrics may be dicts
        pool_total=_maybe_get(meta.evidence_counts, "pool", "total"),
        prompt_total=_maybe_get(meta.evidence_counts, "prompt_included", "total"),
        payload_total=_maybe_get(meta.evidence_counts, "payload_serialized", "total"),
        prompt_truncation=_maybe_get(meta.truncation_metrics, "prompt_truncation"),
        # If some log sinks reference IDs, make this safe as well:
        pool_ids=_maybe_get(meta.evidence_sets, "pool_ids"),  # safe even if absent
    )

    payload: Dict[str, Any] = {
        "request": _asdict(meta.request),
        "policy": _asdict(meta.policy),
        "budgets": _asdict(meta.budgets),
        "fingerprints": _asdict(meta.fingerprints),
        "evidence_counts": _asdict(meta.evidence_counts),
        "evidence_sets": _asdict(meta.evidence_sets),
        "selection_metrics": _asdict(meta.selection_metrics),
        "truncation_metrics": _asdict(meta.truncation_metrics),
        "runtime": _asdict(meta.runtime),
        "load_shed": meta.load_shed,
        "policy_trace": meta.policy_trace,
    }
    return payload

