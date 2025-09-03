from __future__ import annotations

# Neither hashlib nor os are used directly in this module.  The fingerprint helpers
# from core_utils.fingerprints provide SHA‑256 hashing, and environment
# configuration is obtained via core_config.  Removing these unused imports
# simplifies the dependency surface.
from pathlib import Path
from typing import Any, Dict

from core_utils import jsonx

from core_utils.fingerprints import canonical_json
from core_logging import trace_span, get_logger
from core_utils.fingerprints import sha256_hex, ensure_sha256_prefix
from core_config import get_settings
try:
    # Prefer unified schema cache (no blocking I/O; read-only access)
    from .schema_cache import get_cached as _schema_get_cached
except Exception:  # defensive
    _schema_get_cached = None

logger = get_logger("gateway.prompt_envelope")

# Unified API path for the registry mirror
_POLICY_REGISTRY_API_PATH = "/api/policy/registry"

# --------------------------------------------------------------#
#  Lazy-loaded policy registry with flexible path resolution    #
# --------------------------------------------------------------#
_POLICY_REGISTRY: Dict[str, Any] | None = None
_POLICY_REGISTRY_ETAG: str | None = None

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
    env = getattr(get_settings(), "policy_registry_path", None)
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
        # 1) Try unified schema cache first (non-blocking; returns None if empty/expired)
        if _schema_get_cached is not None:
            try:
                cached, _etag = _schema_get_cached(_POLICY_REGISTRY_API_PATH)
                if cached:
                    _POLICY_REGISTRY = cached
                    globals()["_POLICY_REGISTRY_ETAG"] = _etag or ""
                    return _POLICY_REGISTRY
            except Exception:
                # Cache read is best-effort; fall back to local file
                pass

        # 2) Fall back to on‑disk registry (dev/local).  Always parse the
        # file contents via jsonx to guarantee canonical key ordering and
        # consistent numeric handling.  The built‑in json loader is avoided.
        path = _find_registry()
        if path:
            with open(path, "r", encoding="utf-8") as fp:
                # Read the file as UTF‑8 and parse with jsonx.  This raises
                # if the file contains invalid JSON.
                _POLICY_REGISTRY = jsonx.loads(fp.read())
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


_OPTS = jsonx.dumps({"x": 1}).encode()  # noqa: E501 – silences flake8 “unused”


def _sha256(data: bytes) -> str:
    """Return *spec-compliant* SHA-256 fingerprint (“sha256:<hex>”)."""
    return ensure_sha256_prefix(sha256_hex(data))


@trace_span("prompt", logger=logger)
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
        "policy_registry_etag": globals().get("_POLICY_REGISTRY_ETAG", ""),
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

try:
    _load_policy_registry()
except Exception:
    # Best-effort preloading; continue without failing if the registry
    # cannot be loaded at import time.
    pass

