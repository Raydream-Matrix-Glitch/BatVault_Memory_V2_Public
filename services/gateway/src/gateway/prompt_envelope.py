from __future__ import annotations
from core_utils.fingerprints import prompt_fingerprint
from typing import Dict, Any, List, Tuple
import hashlib, json, uuid
from importlib import resources
from pathlib import Path

# --------------------------------------------------------------#
#  Single-source policy registry (JSON) — resolves B-5          #
# --------------------------------------------------------------#
_REG_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_registry.json"
with open(_REG_PATH, "r", encoding="utf-8") as _fp:
    _POLICY_REGISTRY = json.load(_fp)

def build_envelope(
    intent: str,
    question: str,
    evidence: Dict[str, Any],
    allowed_ids: List[str],
    policy_name: str = "why_v1",
    max_tokens: int = 256,
    temperature: float = 0.0,
    retries: int = 0,
    constraint_schema: str = "WhyDecisionAnswer@1",
) -> Tuple[Dict[str, Any], str, str]:
    """
    Returns (envelope, prompt_fingerprint, bundle_fingerprint) — spec §8.2.
    """
    pol = _POLICY_REGISTRY[policy_name]
    envelope = {
        "prompt_id":  pol["prompt_id"],
        "policy_id":  pol["policy_id"],
        "intent":     intent,
        "question":   question,
        "evidence":   evidence,
        "allowed_ids": allowed_ids,
        "policy":    {"temperature": temperature, "retries": retries},
        "explanations": pol.get("explanations", []),
        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
    }
    prompt_fp  = prompt_fingerprint(envelope)
    bundle_fp  = hashlib.sha256(
        json.dumps(evidence, separators=(",", ":")).encode()
    ).hexdigest()
    return envelope, prompt_fp, bundle_fp
