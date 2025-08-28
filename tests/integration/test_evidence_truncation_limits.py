"""
Integration-level guard-rail for **evidence size management** (§Milestone 3).
Forces tiny prompt budgets so we can assert that `selector_truncation` fires
and that the final evidence bundle stays within MAX_PROMPT_BYTES.
"""

import importlib, os
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionTransitions,
    WhyDecisionEvidence,
)

# ── Patch constants *before* importing the selector ────────────────────────────
os.environ["MAX_PROMPT_BYTES"] = "256"
os.environ["SELECTOR_TRUNCATION_THRESHOLD"] = "128"
selector = importlib.import_module("gateway.selector")


def _oversized_evidence() -> WhyDecisionEvidence:
    anchor = WhyDecisionAnchor(id="dummy-anchor")
    events = [
        {"id": f"ev-{i}", "summary": "█" * 1024, "timestamp": "2024-01-01T00:00:00Z"}
        for i in range(16)  # ≈16 KB
    ]
    return WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        transitions=WhyDecisionTransitions(),
        allowed_ids=[],
    )


def test_selector_truncates_when_bundle_exceeds_budget():
    ev_in = _oversized_evidence()
    original_cnt = len(ev_in.events) + 1  # +anchor

    ev_out, meta = selector.truncate_evidence(ev_in)

    # Hard guarantee: bundle now under patched MAX_PROMPT_BYTES
    assert meta["bundle_size_bytes"] <= selector.MAX_PROMPT_BYTES
    # Confirm selector flagged the truncation & actually removed items
    assert meta["selector_truncation"] is True
    assert 1 <= meta["final_evidence_count"] < original_cnt