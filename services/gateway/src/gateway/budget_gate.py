from __future__ import annotations
import os, logging, hashlib
from typing import Any, Dict

from core_logging import log_stage
from core_utils.fingerprints import canonical_json
from core_config.constants import (
    CONTROL_CONTEXT_WINDOW,
    CONTROL_PROMPT_GUARD_TOKENS,
    CONTROL_COMPLETION_TOKENS,
    GATE_COMPLETION_SHRINK_FACTOR,
    GATE_SHRINK_JITTER_PCT,
    GATE_MAX_SHRINK_RETRIES,
)
from core_models.models import GatePlan
from shared.prompt_budget import gate_budget
from gateway.prompt_messages import build_messages
from . import selector as selector_mod

logger = logging.getLogger(__name__)

def _blake3_or_sha256(b: bytes) -> str:
    try:
        import blake3  # type: ignore
        return "blake3:" + blake3.blake3(b).hexdigest()
    except Exception:
        return "sha256:" + hashlib.sha256(b).hexdigest()

def run_gate(envelope: Dict[str, Any], evidence_obj: Any, *, request_id: str, model_name: str|None=None) -> tuple[GatePlan, Any]:
    # Deterministic seed from canonical envelope
    seed_bytes = canonical_json({"intent": envelope.get("intent"), "question": envelope.get("question")})
    seed_int = int(hashlib.sha256(seed_bytes).hexdigest()[0:8], 16)

    gp_dict, trimmed_evidence = gate_budget(
        render_fn=build_messages,
        truncate_fn=selector_mod.truncate_evidence,
        envelope=envelope,
        evidence_obj=evidence_obj,
        context_window=CONTROL_CONTEXT_WINDOW,
        guard_tokens=CONTROL_PROMPT_GUARD_TOKENS,
        desired_completion_tokens=CONTROL_COMPLETION_TOKENS,
        max_retries=GATE_MAX_SHRINK_RETRIES,
        shrink_factor=GATE_COMPLETION_SHRINK_FACTOR,
        jitter_pct=GATE_SHRINK_JITTER_PCT,
        seed=seed_int,
    )
    fp_bytes = canonical_json({"messages": gp_dict["messages"], "model": model_name or os.getenv("VLLM_MODEL_NAME") or "unknown", "stop": None})
    prompt_fingerprint = _blake3_or_sha256(fp_bytes)

    try:
        log_stage(logger, "gate", "plan", request_id=request_id,
                  overhead_tokens=gp_dict["overhead_tokens"],
                  evidence_tokens=gp_dict["evidence_tokens"],
                  desired_completion_tokens=gp_dict["desired_completion_tokens"])
    except Exception:
        pass

    for i, shr in enumerate(gp_dict.get("shrinks", []), start=1):
        try:
            log_stage(logger, "gate", "shrink", request_id=request_id, attempt=i, to_tokens=shr)
        except Exception:
            pass

    try:
        log_stage(logger, "gate", "final", request_id=request_id,
                  prompt_tokens=gp_dict["prompt_tokens"],
                  max_tokens=gp_dict["max_tokens"],
                  prompt_fingerprint=prompt_fingerprint)
    except Exception:
        pass

    gp = GatePlan(**{**gp_dict, "fingerprints": {"prompt": prompt_fingerprint}})
    return gp, trimmed_evidence
