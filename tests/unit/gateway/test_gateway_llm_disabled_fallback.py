"""
Integration test for Gateway fallback when the LLM is disabled.

This test verifies that when ``OPENAI_DISABLED`` is set and the evidence
contains a rationale and a recent event, the Gateway produces a
deterministic short answer derived from that rationale rather than a
placeholder stub.  The fallback must include the rationale, mention the
latest event, be non-empty, and never contain the phrase "STUB ANSWER".  It
must also respect the 320 character limit.
"""

import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
)


@pytest.mark.asyncio
async def test_gateway_fallback_uses_rationale(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force deterministic fallback by disabling the LLM
    monkeypatch.setenv("OPENAI_DISABLED", "1")

    # Stub the evidence builder to return an anchor with a rationale and an event
    async def _dummy_build(anchor_id: str):  # pragma: no cover
        anchor = WhyDecisionAnchor(id=anchor_id, rationale="Because of reasons.")
        events = [
            {
                "id": f"{anchor_id}-E1",
                "type": "event",
                "timestamp": "2025-01-02T00:00:00Z",
                "summary": "An important milestone",
            }
        ]
        evidence = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
        )
        # allowed_ids will be computed by the builder; leave unset here
        evidence.snapshot_etag = "stub-etag"
        return evidence

    # Patch the global evidence builder instance used by gateway.app
    monkeypatch.setattr(
        gw_app._evidence_builder,
        "build",
        _dummy_build,
        raising=True,
    )

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "A1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    short = body["answer"]["short_answer"]
    # Must not be empty and must not contain stub markers
    assert short, "short_answer must not be empty"
    assert "STUB ANSWER" not in short, "short_answer must not contain stub markers"
    # Must start with the anchor rationale and reference the most recent event
    assert short.startswith("Because of reasons"), "short_answer must include the anchor rationale"
    assert "An important milestone" in short, "short_answer must mention the latest event summary"
    # Must respect the 320 character length limit
    assert len(short) <= 320, "short_answer must be truncated to 320 characters"