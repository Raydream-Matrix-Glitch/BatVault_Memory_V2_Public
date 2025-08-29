"""Tests for the shared meta builder.

These tests verify that the pure meta construction helper behaves
deterministically and normalises fingerprint prefixes.  They also
ensure that the returned meta object does not contain duplicate
keys when serialised to a dictionary.

The build_meta function is intentionally free of side effects: it
must not depend on the request identifier beyond structured logging.
By constructing identical ``MetaInputs`` instances and invoking
``build_meta`` multiple times we can assert that the outputs are
deeply equal.  Additionally, the helper must prepend ``sha256:`` to
fingerprints when absent and reject any duplicate keys in the
serialised representation.
"""

from __future__ import annotations

from core_models.meta_inputs import MetaInputs
from shared.meta_builder import build_meta


def _make_inputs(**overrides):
    """Return a MetaInputs instance with sensible defaults.

    The defaults cover all required fields.  Callers may override
    individual fields via keyword arguments.
    """
    base = dict(
        policy_id="p0",
        prompt_id="pr0",
        prompt_fingerprint="deadbeef",
        bundle_fingerprint="bf0",
        bundle_size_bytes=123,
        prompt_tokens=10,
        evidence_tokens=5,
        max_tokens=20,
        snapshot_etag="etag0",
        gateway_version="1.0.0",
        selector_model_id="sel0",
        fallback_used=False,
        fallback_reason=None,
        retries=0,
        latency_ms=100,
        validator_error_count=0,
        evidence_metrics={"total_neighbors_found": 0, "selector_truncation": 0, "final_evidence_count": 0},
        load_shed=False,
        events_total=0,
        events_truncated=False,
    )
    base.update(overrides)
    return MetaInputs(**base)


def test_build_meta_idempotency() -> None:
    """Identical inputs must produce deeply equal MetaInfo objects."""
    inputs = _make_inputs()
    meta1 = build_meta(inputs, request_id="reqA")
    meta2 = build_meta(inputs, request_id="reqB")
    # Pydantic models implement structural equality
    assert meta1 == meta2, "MetaInfo objects differ for identical inputs"
    # Serialised dictionaries must also match exactly
    assert meta1.model_dump(mode="python") == meta2.model_dump(mode="python")


def test_build_meta_normalises_fingerprint_and_keys_unique() -> None:
    """The builder must prefix fingerprints and avoid duplicate keys."""
    # Provide a fingerprint without the sha256: prefix
    inputs = _make_inputs(prompt_fingerprint="abc123", selector_model_id=None, fallback_used=True, fallback_reason="llm_off")
    meta = build_meta(inputs, request_id="reqX")
    # The fingerprint should be normalised to start with sha256:
    assert meta.prompt_fingerprint.startswith("sha256:"), "Fingerprint prefix not normalised"
    # Serialising to a dict should yield unique keys only
    d = meta.model_dump(mode="python")
    keys = list(d.keys())
    assert len(keys) == len(set(keys)), f"Duplicate keys found in meta: {keys}"

