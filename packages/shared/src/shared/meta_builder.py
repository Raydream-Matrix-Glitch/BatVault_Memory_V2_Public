"""
Pure helper for constructing canonical meta objects.

This module exposes a single function, :func:`build_meta`, which
accepts a :class:`core_models.meta_inputs.MetaInputs` instance and a
request identifier.  It normalises fingerprint prefixes, instantiates
a validated :class:`core_models.models.MetaInfo` and emits a structured
log entry.  No I/O or side effects beyond logging are performed.
"""

from typing import Any, Dict

from core_logging import get_logger, log_stage
from core_models.meta_inputs import MetaInputs
from core_models.models import MetaInfo


_logger = get_logger("shared.meta_builder")


def _normalise_prompt_fingerprint(fp: str) -> str:
    """
    Normalise the prompt fingerprint to include a ``sha256:`` prefix.

    If *fp* already begins with ``sha256:``, it is returned unchanged.
    Otherwise the prefix is added.  The hash portion itself is *not*
    recomputed: callers must provide the correct digest.
    """
    if fp.lower().startswith("sha256:"):
        return fp
    return f"sha256:{fp}"


def build_meta(inputs: MetaInputs, *, request_id: str) -> MetaInfo:
    """
    Construct a canonical :class:`MetaInfo` from the supplied
    :class:`MetaInputs`.

    This helper is deterministic and idempotent: invoking it multiple
    times with equivalent input will yield deep-equal outputs.  It does
    not perform any network or disk I/O.  The only observable side
    effect is a structured ``meta.built`` log entry.

    Parameters
    ----------
    inputs:
        A validated :class:`MetaInputs` instance containing all
        telemetry fields required to construct a ``MetaInfo``.
    request_id:
        The current request identifier to include in audit logs.

    Returns
    -------
    MetaInfo
        A validated meta object ready for inclusion in a response.
    """
    # Ensure the fingerprint has the expected prefix; do not recompute.
    prompt_fp = _normalise_prompt_fingerprint(inputs.prompt_fingerprint)
    # Assemble a dict for construction; we do not modify the original inputs.
    data: Dict[str, Any] = inputs.model_dump(mode="python")
    data["prompt_fingerprint"] = prompt_fp

    # Instantiate MetaInfo; Pydantic validates and forbids extras.
    meta = MetaInfo(**data)

    # Emit structured info log for auditability.  Include key fields.
    try:
        log_stage(
            _logger,
            "meta",
            "built",
            request_id=request_id,
            prompt_fingerprint=meta.prompt_fingerprint,
            gateway_version=meta.gateway_version,
            selector_model_id=meta.selector_model_id,
            events_total=meta.events_total,
            events_truncated=meta.events_truncated,
            retries=meta.retries,
            fallback_used=meta.fallback_used,
            fallback_reason=meta.fallback_reason,
        )
    except Exception:
        # Logging must never raise; swallow all failures
        pass

    return meta