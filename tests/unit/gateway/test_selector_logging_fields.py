import logging

import pytest

from core_config.constants import SELECTOR_TRUNCATION_THRESHOLD
from gateway.selector import (
    truncate_evidence,
    bundle_size_bytes,
    WhyDecisionEvidence,
    WhyDecisionAnchor,
)


def _oversized_evidence() -> WhyDecisionEvidence:
    """Build an evidence bundle that *must* be truncated."""
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="A"))
    # Keep adding dummy events until we are well over the soft limit.
    while bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD + 256:
        idx = len(ev.events)
        ev.events.append(
            {
                "id": f"E{idx}",
                "timestamp": "2025-07-01T00:00:00Z",
                "summary": "x" * 1024,
            }
        )
    return ev


def test_selector_emits_required_log_fields(caplog: pytest.LogCaptureFixture):
    ev = _oversized_evidence()

    with caplog.at_level(logging.INFO):
        truncate_evidence(ev)

    # pick the first selector_complete record
    records = [r for r in caplog.records if r.message == "selector_complete"]
    assert records, "selector_complete log record missing"
    rec = records[0]

    # Mandatory metrics per tech-spec B5
    assert hasattr(rec, "selector_truncation")
    assert hasattr(rec, "selector_model_id")
    assert hasattr(rec, "dropped_evidence_ids")

    # And because we engineered an oversize bundle, truncation must be True
    assert rec.selector_truncation is True
    assert isinstance(rec.dropped_evidence_ids, list) and rec.dropped_evidence_ids