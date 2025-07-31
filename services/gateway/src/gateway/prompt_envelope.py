from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from core_utils.fingerprints import canonical_json

# --------------------------------------------------------------#
#  Lazy-loaded policy registry to avoid FS dependency at import #
# --------------------------------------------------------------#
_POLICY_REGISTRY: Dict[str, Any] | None = None


def _load_policy_registry() -> Dict[str, Any]:
    global _POLICY_REGISTRY
    if _POLICY_REGISTRY is None:
        reg_path = Path(__file__).resolve().parent.parent / "config" / "policy_registry.json"
        with open(reg_path, "r", encoding="utf-8") as fp:
            _POLICY_REGISTRY = json.load(fp)
    return _POLICY_REGISTRY

_OPTS = json.dumps({"x": 1}).encode()  # noqa: E501 – silence unused-import flake

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_prompt_envelope(
    question: str,
    evidence: Dict[str, Any],
    snapshot_etag: str,
    **kw,
) -> Dict[str, Any]:
    """
    Return **canonical** prompt envelope with deterministic fingerprints.
    """
    pol = _load_policy_registry()[kw.get("policy_name", "why_v1")]

    env: Dict[str, Any] = {
        "prompt_version": kw.get("prompt_version", "why_v1"),
        "intent": kw.get("intent", "why_decision"),
        "prompt_id": pol["prompt_id"],
        "policy_id": pol["policy_id"],
        "question": question,
        "evidence": evidence,
        "allowed_ids": kw.get("allowed_ids", []),
        "policy": {"temperature": kw.get("temperature", 0.0),
                   "retries": kw.get("retries", 0)},
        "explanations": pol.get("explanations", {}),
        "constraints": {
            "output_schema": kw.get("constraint_schema", "WhyDecisionAnswer@1"),
            "max_tokens": kw.get("max_tokens", 256),
        },
    }

    bundle_fp = _sha256(canonical_json(evidence))
    prompt_fp = _sha256(canonical_json(env))

    env["_fingerprints"] = {
        "bundle_fingerprint": bundle_fp,
        "prompt_fingerprint": prompt_fp,
        "snapshot_etag": snapshot_etag,
    }
    return env
