from __future__ import annotations

import datetime as dt

from gateway.selector import (
    MAX_PROMPT_BYTES,
    _sim,
    bundle_size_bytes,
    truncate_evidence,
)
from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _dummy_ev(event_count: int = 20) -> WhyDecisionEvidence:
    """Return an in-memory evidence bundle with <event_count> 512-byte events."""
    anchor = WhyDecisionAnchor(id="a1", option="opt-x", rationale="foo bar")
    events = [
        {
            "id": f"e{i}",
            "summary": "x" * 512,
            "timestamp": dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).isoformat(),
        }
        for i in range(event_count)
    ]
    return WhyDecisionEvidence(
        anchor=anchor,
        events=events,
        allowed_ids=[e["id"] for e in events] + [anchor.id],
        transitions={"preceding": [], "succeeding": []},
    )


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_sim_non_zero() -> None:
    """Basic sanity — similarity > 0 when tokens overlap."""
    assert _sim("foo bar baz", "foo") > 0.0


def test_truncate_respects_limit() -> None:
    """truncate_evidence must never exceed MAX_PROMPT_BYTES."""
    ev = _dummy_ev()
    ev, meta = truncate_evidence(ev)
    assert bundle_size_bytes(ev) <= MAX_PROMPT_BYTES
    assert meta["final_evidence_count"] >= 1
    # If truncation happened, selector_truncation must be flagged.
    if meta["selector_truncation"]:
        assert meta["dropped_evidence_ids"]
