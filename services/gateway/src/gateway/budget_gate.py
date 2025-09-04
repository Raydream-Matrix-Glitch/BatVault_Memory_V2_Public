import hashlib
from typing import Any, Dict
from core_config import get_settings
from core_logging import get_logger

from core_utils.fingerprints import (
    sha256_hex,
    ensure_sha256_prefix,
    canonical_json,
)
from .logging_helpers import stage as log_stage
from core_config.constants import (
    CONTROL_CONTEXT_WINDOW,
    CONTROL_PROMPT_GUARD_TOKENS,
    CONTROL_COMPLETION_TOKENS,
    GATE_COMPLETION_SHRINK_FACTOR,
    GATE_SHRINK_JITTER_PCT,
    GATE_MAX_SHRINK_RETRIES,
    GATE_SAFETY_HEADROOM_TOKENS,
)
from core_models.models import GatePlan
from shared.prompt_budget import gate_budget
from gateway.prompt_messages import build_messages

logger = get_logger("gateway.budget_gate")

def _blake3_or_sha256(b: bytes) -> str:
    """
    Compute a deterministic fingerprint for the given bytes.

    Historically the Gateway preferred blake3 fingerprints when the
    dependency was available, falling back to SHA‑256 otherwise.  This
    variation resulted in inconsistent prefixes in the final payload
    (e.g. ``blake3:…`` vs ``sha256:…``) depending on the runtime
    environment.  To enforce a stable contract and simplify downstream
    parsing the fingerprint routine now unconditionally computes a
    SHA-256 digest and prefixes it with ``sha256:``.
    """
    return ensure_sha256_prefix(sha256_hex(b))

def run_gate(envelope: Dict[str, Any], evidence_obj: Any, *, request_id: str, model_name: str|None=None) -> tuple[GatePlan, Any]:
    """
    Deterministically budget a prompt and evidence bundle based on the supplied
    envelope and evidence object.  This variant dynamically computes the
    context window and completion budget from configuration instead of
    relying on hard‑coded constants.  Environment variables such as
    ``VLLM_MAX_MODEL_LEN`` and ``LLM_MAX_TOKENS`` may override the default
    control model parameters, allowing larger or smaller budgets per model.
    """
    # Deterministic seed from canonical envelope
    seed_bytes = canonical_json({"intent": envelope.get("intent"), "question": envelope.get("question")})
    seed_int = int(hashlib.sha256(seed_bytes).hexdigest()[0:8], 16)

    try:
        setattr(evidence_obj, "_request_id", request_id)
    except Exception:
        pass

    # Derive dynamic context window and desired completion tokens from a single
    # settings lookup.  Avoid repeatedly calling get_settings() during hot
    # path execution; this ensures consistent values and improves testability.
    _s = None
    try:
        _s = get_settings()
    except Exception:
        _s = None
    if _s is not None:
        try:
            dynamic_context = int(getattr(_s, "vllm_max_model_len", None) or CONTROL_CONTEXT_WINDOW)
        except Exception:
            dynamic_context = CONTROL_CONTEXT_WINDOW
        try:
            dynamic_completion = int(getattr(_s, "llm_max_tokens", None) or CONTROL_COMPLETION_TOKENS)
        except Exception:
            dynamic_completion = CONTROL_COMPLETION_TOKENS
    else:
        dynamic_context = CONTROL_CONTEXT_WINDOW
        dynamic_completion = CONTROL_COMPLETION_TOKENS

    try:
        headroom = int(getattr(_s, 'gate_safety_headroom_tokens', None) or GATE_SAFETY_HEADROOM_TOKENS)
    except Exception:
        headroom = GATE_SAFETY_HEADROOM_TOKENS
    # Ensure the effective context never drops below guard+64 so we always allow a minimal completion.
    effective_context = max(CONTROL_PROMPT_GUARD_TOKENS + 64, int(dynamic_context) - int(headroom))

    gp_dict, trimmed_evidence = gate_budget(
        render_fn=build_messages,
        truncate_fn=authoritative_truncate,
        envelope=envelope,
        evidence_obj=evidence_obj,
        context_window=effective_context,
        guard_tokens=CONTROL_PROMPT_GUARD_TOKENS,
        desired_completion_tokens=dynamic_completion,
        max_retries=GATE_MAX_SHRINK_RETRIES,
        shrink_factor=GATE_COMPLETION_SHRINK_FACTOR,
        jitter_pct=GATE_SHRINK_JITTER_PCT,
        seed=seed_int,
    )
    # Compute prompt fingerprint deterministically; reuse cached settings when possible
    model_label = model_name
    if not model_label:
        try:
            model_label = getattr(_s, "vllm_model_name", None) if _s is not None else None
        except Exception:
            model_label = None
    if not model_label:
        model_label = "unknown"
    fp_bytes = canonical_json({"messages": gp_dict["messages"], "model": model_label, "stop": None})
    prompt_fingerprint = _blake3_or_sha256(fp_bytes)

    try:
        log_stage("gate", "plan", request_id=request_id,
                  overhead_tokens=gp_dict["overhead_tokens"],
                  evidence_tokens=gp_dict["evidence_tokens"],
                  desired_completion_tokens=gp_dict["desired_completion_tokens"])
    except Exception:
        pass

    for i, shr in enumerate(gp_dict.get("shrinks", []), start=1):
        try:
            log_stage("gate", "shrink", request_id=request_id, attempt=i, to_tokens=shr)
        except Exception:
            pass

    try:
        log_stage("gate", "final", request_id=request_id,
                  prompt_tokens=gp_dict["prompt_tokens"],
                  max_tokens=gp_dict["max_tokens"],
                  prompt_fingerprint=prompt_fingerprint)
    except Exception:
        pass

    gp = GatePlan(**{**gp_dict, "fingerprints": {"prompt": prompt_fingerprint}})
    return gp, trimmed_evidence

def authoritative_truncate(
    evidence_obj,
    *,
    overhead_tokens: int = 0,
    desired_completion_tokens: int | None = None,
    context_window: int | None = None,
    guard_tokens: int | None = None,
) -> tuple:
    """
    Authoritative token-budgeting + trimming loop.

    - Computes max_prompt_tokens = context_window - desired_completion_tokens - guard_tokens
    - Drops least-relevant events (based on selector.rank_events) until the prompt fits
    - Produces meta compatible with previous selector output
    """
    from .selector import evidence_prompt_tokens, rank_events
    from core_models.models import WhyDecisionEvidence

    # Work on a deep copy so the caller keeps the original
    ev: WhyDecisionEvidence = evidence_obj.model_copy(deep=True)
    request_id = getattr(ev, "_request_id", None)

    # If gate knobs missing, just pass-through with canonical allowed_ids
    if (
        desired_completion_tokens is None
        or context_window is None
        or guard_tokens is None
    ):
        try:
            from core_validator import canonical_allowed_ids as _canon
            aid = getattr(ev.anchor, "id", "") or ""
            events = [e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python") for e in (ev.events or [])]
            transitions = []
            tr = getattr(ev, "transitions", None)
            if tr is not None:
                transitions.extend(getattr(tr, "preceding", []) or [])
                transitions.extend(getattr(tr, "succeeding", []) or [])
                transitions = [t if isinstance(t, dict) else getattr(t, "model_dump", dict)(mode="python") for t in transitions]
            ev.allowed_ids = _canon(aid, events, transitions)
        except Exception:
            pass
        meta = {
            "selector_truncation": False,
            "total_neighbors_found": max(len(ev.allowed_ids or []) - 1, 0),
            "final_evidence_count": len(ev.allowed_ids or []),
            "dropped_evidence_ids": [],
            "prompt_tokens": overhead_tokens + evidence_prompt_tokens(ev),
            "max_prompt_tokens": None,
            "bundle_size_bytes": 0,
        }
        try:
            log_stage("gate", "selector_complete", request_id=request_id, **meta)
        except Exception:
            pass
        return ev, meta

    max_prompt_tokens = max(256, int(context_window) - int(desired_completion_tokens) - int(guard_tokens))

    # If already within budget, just set allowed_ids canonically
    if overhead_tokens + evidence_prompt_tokens(ev) <= max_prompt_tokens:
        try:
            from core_validator import canonical_allowed_ids as _canon
            aid = getattr(ev.anchor, "id", "") or ""
            events = [e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python") for e in (ev.events or [])]
            transitions = []
            tr = getattr(ev, "transitions", None)
            if tr is not None:
                transitions.extend(getattr(tr, "preceding", []) or [])
                transitions.extend(getattr(tr, "succeeding", []) or [])
                transitions = [t if isinstance(t, dict) else getattr(t, "model_dump", dict)(mode="python") for t in transitions]
            ev.allowed_ids = _canon(aid, events, transitions)
        except Exception:
            pass
        meta = {
            "selector_truncation": False,
            "total_neighbors_found": max(len(ev.allowed_ids or []) - 1, 0),
            "final_evidence_count": len(ev.allowed_ids or []),
            "dropped_evidence_ids": [],
            "prompt_tokens": overhead_tokens + evidence_prompt_tokens(ev),
            "max_prompt_tokens": max_prompt_tokens,
            "bundle_size_bytes": 0,
        }
        try:
            log_stage("gate", "selector_complete", request_id=request_id, **meta)
        except Exception:
            pass
        return ev, meta

    # Deterministic prune loop (drop least-relevant events only)
    dropped_ids: list[str] = []
    # Build a ranked list of event dicts (most relevant first)
    try:
        events_dicts = []
        for e in (ev.events or []):
            try:
                events_dicts.append(e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python"))
            except Exception:
                events_dicts.append(dict(e))
        ranked = rank_events(ev.anchor, events_dicts)
        # We'll drop from the end (least relevant)
        drop_order_ids = [d.get("id") for d in ranked if d.get("id")]
    except Exception:
        drop_order_ids = [ (getattr(e, "get", lambda k: None)("id") if isinstance(e, dict) else getattr(e, "id", None)) for e in (ev.events or []) ]
        drop_order_ids = [i for i in drop_order_ids if i]
    # Loop until within budget or no events left
    while overhead_tokens + evidence_prompt_tokens(ev) > max_prompt_tokens and drop_order_ids:
        victim_id = drop_order_ids.pop()
        try:
            ev.events = [e for e in (ev.events or []) if ((e.get("id") if isinstance(e, dict) else getattr(e, "id", None)) != victim_id)]
            dropped_ids.append(victim_id)
        except Exception:
            # best effort removal
            pass

    # Set canonical allowed_ids based on remaining items
    try:
        from core_validator import canonical_allowed_ids as _canon
        aid = getattr(ev.anchor, "id", "") or ""
        events = [e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python") for e in (ev.events or [])]
        transitions = []
        tr = getattr(ev, "transitions", None)
        if tr is not None:
            transitions.extend(getattr(tr, "preceding", []) or [])
            transitions.extend(getattr(tr, "succeeding", []) or [])
            transitions = [t if isinstance(t, dict) else getattr(t, "model_dump", dict)(mode="python") for t in transitions]
        ev.allowed_ids = _canon(aid, events, transitions)
    except Exception:
        pass

    neighbor_count = max(len(ev.allowed_ids or []) - 1, 0)
    final_tokens = overhead_tokens + evidence_prompt_tokens(ev)
    meta = {
        "selector_truncation": len(dropped_ids) > 0,
        "total_neighbors_found": neighbor_count,
        "final_evidence_count": len(ev.allowed_ids or []),
        "dropped_evidence_ids": dropped_ids,
        "prompt_tokens": final_tokens,
        "max_prompt_tokens": max_prompt_tokens,
        "bundle_size_bytes": 0,
    }
    try:
        log_stage("gate", "selector_complete", request_id=request_id, **meta)
    except Exception:
        pass
    return ev, meta
