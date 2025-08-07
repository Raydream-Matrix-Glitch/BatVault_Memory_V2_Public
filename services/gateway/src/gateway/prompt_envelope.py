from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from core_utils.fingerprints import canonical_json
from core_logging import trace_span

# --------------------------------------------------------------#
#  Lazy-loaded policy registry with flexible path resolution    #
# --------------------------------------------------------------#
_POLICY_REGISTRY: Dict[str, Any] | None = None


def _find_registry() -> Path | None:
    """
    Locate the authoritative ``policy_registry.json``.

    Search order (first hit wins):
      1. Environment variable ``POLICY_REGISTRY_PATH``.
      2. Same directory as *this* module.
      3. Parent directory.
      4. ``<parent>/config/policy_registry.json`` (legacy location).

    Returns
    -------
    pathlib.Path | None
        Path to the registry file, or *None* if nothing found.
    """
    env = os.getenv("POLICY_REGISTRY_PATH")
    if env and Path(env).is_file():
        return Path(env)

    here = Path(__file__).resolve().parent
    candidates = [
        here / "policy_registry.json",
        here.parent / "policy_registry.json",
        here.parent / "config" / "policy_registry.json",
        here.parent.parent / "config" / "policy_registry.json",   # ← covers services/gateway/config
    ]
    return next((p for p in candidates if p.is_file()), None)


def _load_policy_registry() -> Dict[str, Any]:
    """
    Lazy-load and memoise the policy registry.

    Falls back to a minimal default map so that unit tests and
    development environments can boot without the real file.
    """
    global _POLICY_REGISTRY
    if _POLICY_REGISTRY is None:
        path = _find_registry()
        if path:
            with open(path, "r", encoding="utf-8") as fp:
                _POLICY_REGISTRY = json.load(fp)
        else:
            # Graceful fallback keeps Gateway operable when the
            # registry hasn’t been provisioned yet.
            _POLICY_REGISTRY = {
                "why_v1": {
                    "prompt_id": "why_v1.default",
                    "policy_id": "why_v1.policy",
                    "json_mode": True,
                    "temperature": 0.0,
                    "retries": 2,
                    "max_tokens": 256,
                    "explanations": {},
                }
            }
    return _POLICY_REGISTRY


_OPTS = json.dumps({"x": 1}).encode()  # noqa: E501 – silences flake8 “unused”


def _sha256(data: bytes) -> str:
    """Return *spec-compliant* SHA-256 fingerprint (“sha256:<hex>”)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


@trace_span("prompt")
def build_prompt_envelope(
    question: str,
    evidence: Dict[str, Any],
    snapshot_etag: str,
    **kw,
) -> Dict[str, Any]:
    """
    Build a **canonical** prompt envelope with deterministic fingerprints.

    Parameters
    ----------
    question : str
        Natural-language question to ask the model.
    evidence : dict
        Evidence bundle (already validated).
    snapshot_etag : str
        Storage snapshot tag for traceability.
    kw :
        Optional overrides:
        ``policy_name``, ``prompt_version``, ``intent``, ``allowed_ids``,
        ``temperature``, ``retries``, ``constraint_schema``, ``max_tokens``.

    Returns
    -------
    dict
        Prompt envelope ready for core-LLM dispatcher.
    """
    registry = _load_policy_registry()
    policy_name = kw.get("policy_name", "why_v1")
    pol = registry.get(policy_name)
    if pol is None:  # defensive – guarantees downstream key access
        raise KeyError(f"Unknown policy_name '{policy_name}'")

    env: Dict[str, Any] = {
        "prompt_version": kw.get("prompt_version", "why_v1"),
        "intent": kw.get("intent", "why_decision"),
        "prompt_id": pol["prompt_id"],
        "policy_id": pol["policy_id"],
        "question": question,
        "evidence": evidence,
        "allowed_ids": kw.get("allowed_ids", []),
        "policy": {
            "temperature": kw.get("temperature", pol.get("temperature", 0.0)),
            "retries": kw.get("retries", pol.get("retries", 0)),
        },
        "explanations": pol.get("explanations", {}),
        "constraints": {
            "output_schema": kw.get("constraint_schema", "WhyDecisionAnswer@1"),
            "max_tokens": kw.get("max_tokens", pol.get("max_tokens", 256)),
        },
    }

    bundle_fp = _sha256(canonical_json(evidence))
    prompt_fp = _sha256(canonical_json(env))

    env["_fingerprints"] = {
        "bundle_fingerprint": bundle_fp,
        "prompt_fingerprint": prompt_fp,
        "snapshot_etag": snapshot_etag,
    }

    # ── expose fingerprints on current OTEL span ──────────────────
    try:
        from opentelemetry import trace as _t

        span = _t.get_current_span()
        if span and span.is_recording():
            span.set_attribute("bundle_fingerprint", bundle_fp)
            span.set_attribute("prompt_fingerprint", prompt_fp)
            span.set_attribute("snapshot_etag", snapshot_etag)
    except ModuleNotFoundError:
        # OTEL optional – ignore if not installed
        pass

    return env

