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

def _as_event_dict(item: Any) -> Dict[str, Any]:
    """Strict conversion to a dict with at least an 'id' key; raises on failure."""
    if isinstance(item, dict):
        if "id" in item and isinstance(item["id"], str) and item["id"]:
            return item
        raise ValueError("event dict missing 'id'")
    if hasattr(item, "model_dump"):
        d = item.model_dump(mode="python")
        if isinstance(d, dict) and d.get("id"):
            return d
        raise ValueError("model_dump missing 'id'")
    raise TypeError(f"unsupported event type: {type(item)}")

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
    - Drops from the tail of the provided selection order until the prompt fits
      (order comes from envelope['selection_order'] if provided, otherwise current evidence order)
    - Produces meta compatible with selector output, with a single prompt_truncation flag
    """
    from .selector import evidence_prompt_tokens
    from core_models.models import WhyDecisionEvidence

    # Work on a deep copy so the caller keeps the original
    ev: WhyDecisionEvidence = evidence_obj.model_copy(deep=True)
    request_id = getattr(ev, "_request_id", None)

    # Emit a precheck snapshot of budget inputs before any pruning happens
    try:
        log_stage("gate", "precheck",
                  request_id=request_id,
                  overhead_tokens=int(overhead_tokens),
                  evidence_tokens=int(evidence_prompt_tokens(ev)),
                  context_window=context_window,
                  desired_completion_tokens=desired_completion_tokens,
                  guard_tokens=guard_tokens)
    except Exception:
        pass

    # If gate knobs missing, just pass-through with canonical allowed_ids
    if (
        desired_completion_tokens is None
        or context_window is None
        or guard_tokens is None
    ):
        # No budget knobs provided: pass-through (include-all policy).
        # Build a prompt meta that explicitly encodes the prompt set (== pool).
        _pool_ids: list[str] = []
        try:
            from core_validator import canonical_allowed_ids as _canon
            aid = getattr(ev.anchor, "id", "") or ""
            events = []
            for _raw in (ev.events or []):
                try:
                    events.append(_as_event_dict(_raw))
                except Exception:
                    continue
            transitions = []
            tr = getattr(ev, "transitions", None)
            if tr is not None:
                transitions.extend(getattr(tr, "preceding", []) or [])
                transitions.extend(getattr(tr, "succeeding", []) or [])
                _tmp = []
                for _t in transitions:
                    try:
                        _tmp.append(_as_event_dict(_t))
                    except Exception:
                        continue
                transitions = _tmp
            _pool_ids = _canon(aid, events, transitions)
        except Exception:
            _pool_ids = []
        meta = {
            "prompt_truncation": False,
            "prompt_included_ids": _pool_ids,
            "prompt_excluded_ids": [],
            "prompt_tokens": overhead_tokens + evidence_prompt_tokens(ev),
            "max_prompt_tokens": None,
            "bundle_size_bytes": 0,
        }
        try:
            log_stage("gate", "selector_complete", request_id=request_id, **meta)
            log_stage("gate", "allowed_ids_preserved", request_id=request_id, allowed_ids_count=len(ev.allowed_ids or []))
        except Exception:
            pass
        return ev, meta

    max_prompt_tokens = max(256, int(context_window) - int(desired_completion_tokens) - int(guard_tokens))

    # Check if already within budget; if so, return unchanged evidence
    current_tokens = overhead_tokens + evidence_prompt_tokens(ev)
    if current_tokens <= max_prompt_tokens:
        # Already within budget; no truncation.
        try:
            from core_validator import canonical_allowed_ids as _canon
            aid = getattr(ev.anchor, "id", "") or ""
            events_dicts = []
            for _raw in (ev.events or []):
                try:
                    events_dicts.append(_as_event_dict(_raw))
                except Exception:
                    continue
            tr = getattr(ev, "transitions", None)
            transitions = []
            if tr is not None:
                transitions.extend(getattr(tr, "preceding", []) or [])
                transitions.extend(getattr(tr, "succeeding", []) or [])
                _tmp = []
                for _t in transitions:
                    try:
                        _tmp.append(_as_event_dict(_t))
                    except Exception:
                        continue
                transitions = _tmp
            _prompt_ids = _canon(aid, events_dicts, transitions)
        except Exception:
            _prompt_ids = []
        meta = {
            "prompt_selector_truncation": False,
            "prompt_included_ids": _prompt_ids,
            "prompt_excluded_ids": [],
            "prompt_tokens": current_tokens,
            "max_prompt_tokens": max_prompt_tokens,
            "bundle_size_bytes": 0,
        }
        try:
            log_stage("gate", "selector_complete", request_id=request_id, **meta)
        except Exception:
            pass
        return ev, meta

    # Deterministic prune loop (drop least-relevant events only)
    try:
        log_stage("gate", "prune_enter",
                  request_id=request_id,
                  prompt_tokens=current_tokens,
                  max_prompt_tokens=max_prompt_tokens,
                  events_count=len(ev.events or []))
    except Exception:
        pass
    dropped_ids: list[str] = []
    # Build a fixed drop order: from envelope['selection_order'] or evidence order
    try:
        events_dicts = []
        for _raw in (ev.events or []):
            try:
                events_dicts.append(_as_event_dict(_raw))
            except Exception:
                # Deterministically skip malformed items
                continue
        sel_order = (locals().get("envelope") or {}).get("selection_order") or []
        if not sel_order:
            sel_order = [d["id"] for d in events_dicts if d.get("id")]
        drop_order_ids = list(sel_order)
    except Exception:
        drop_order_ids = [ (e.get("id") if isinstance(e, dict) else getattr(e, "id", None)) for e in (ev.events or []) ]
        drop_order_ids = [i for i in drop_order_ids if i]
    try:
        log_stage("gate", "selection_order",
                  request_id=request_id,
                  source=("envelope" if (locals().get("envelope") or {}).get("selection_order") else "evidence_order"),
                  count=len(drop_order_ids))
    except Exception:
        pass
    # Loop until within budget or no events left
    def _get_id(it):
        try:
            return it.get("id") if isinstance(it, dict) else getattr(it, "id", None)
        except Exception:
            return None

    while overhead_tokens + evidence_prompt_tokens(ev) > max_prompt_tokens and drop_order_ids:
        victim_id = drop_order_ids.pop()
        # Find and stub the first matching event
        try:
            new_list = []
            stubbed = False
            for _e in (ev.events or []):
                eid = _get_id(_e)
                if not stubbed and eid == victim_id:
                    new_list.append({"id": victim_id})
                    stubbed = True
                else:
                    new_list.append(_e)
            if stubbed:
                ev.events = new_list
                dropped_ids.append(victim_id)
            # If already stubbed (no change), continue to next victim.
        except Exception:
            # best-effort stubbing
            pass

    # Preserve ev.allowed_ids: it's the full k=1 union computed upstream.

    final_tokens = overhead_tokens + evidence_prompt_tokens(ev)
    # Compute prompt-included ids from the trimmed evidence to make prompt≠payload explicit.
    try:
        from core_validator import canonical_allowed_ids as _canon
        aid = getattr(ev.anchor, "id", "") or ""
        events_dicts = []
        for _raw in (ev.events or []):
            try:
                events_dicts.append(_as_event_dict(_raw))
            except Exception:
                continue
        tr = getattr(ev, "transitions", None)
        transitions = []
        if tr is not None:
            transitions.extend(getattr(tr, "preceding", []) or [])
            transitions.extend(getattr(tr, "succeeding", []) or [])
            _tmp = []
            for _t in transitions:
                try:
                    _tmp.append(_as_event_dict(_t))
                except Exception:
                    continue
            transitions = _tmp
        _prompt_ids = _canon(aid, events_dicts, transitions)
    except Exception:
        _prompt_ids = []

    meta = {
        "prompt_truncation": len(dropped_ids) > 0,
        "prompt_included_ids": _prompt_ids,
        "prompt_excluded_ids": [{"id": i, "reason": "token_budget"} for i in dropped_ids],
        "prompt_tokens": final_tokens,
        "max_prompt_tokens": max_prompt_tokens,
        "bundle_size_bytes": 0,
    }
    try:
        log_stage("gate", "selector_complete", request_id=request_id, **meta)
        log_stage("gate", "prune_exit",
                  request_id=request_id,
                  dropped=len(dropped_ids),
                  final_prompt_tokens=final_tokens,
                  max_prompt_tokens=max_prompt_tokens,
                  remaining_events=len(ev.events or []))
        # Strategic: sample IDs in logs for audit debugging without spamming
        if dropped_ids:
            logger.info("gate.prune_exit.ids",
                        extra={"request_id": request_id,
                               "dropped_ids_sample": dropped_ids[:10],
                               "prompt_included_ids_sample": meta.get("prompt_included_ids", [])[:10]})
    except Exception:
        pass
    return ev, meta
