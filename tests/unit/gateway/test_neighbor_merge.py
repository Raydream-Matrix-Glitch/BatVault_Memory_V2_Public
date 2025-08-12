"""Unit test for merging router helper neighbours into the evidence bundle.

This test feeds mixed neighbour types (events, decisions and transitions)
into the `/v2/query` route and asserts that:

* Only **true events** are accumulated in the `events` list — neighbouring
  decisions are recognised as decisions and **dropped** from the build context.
* Transitions are split into `preceding` and `succeeding` lists based on the
  anchor's role in the link (using `from`/`to` fields or falling back to
  edge orientation hints).
* The `allowed_ids` field contains exactly the anchor ID plus the IDs of
  events and transitions present (decisions are **not** included).

The gateway builder is patched to avoid invoking the LLM; the evidence
returned by the route is inspected directly in the JSON response.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app


@pytest.mark.asyncio
async def test_mixed_neighbour_merge(monkeypatch):
    """Verify that the v2 query endpoint correctly classifies mixed neighbours."""

    # Anchor ID used in this test
    anchor_id = "anchor1"

    # Patch the decision resolver to always return our anchor
    async def _stub_resolver(text: str):  # pragma: no cover
        return {"id": anchor_id}

    monkeypatch.setattr(gw_app, "resolve_decision_text", _stub_resolver, raising=True)

    # Patch EvidenceBuilder.build to return a minimal empty evidence bundle
    from core_models.models import (
        WhyDecisionEvidence,
        WhyDecisionAnchor,
        WhyDecisionTransitions,
    )

    async def _stub_build(self, aid: str, include_neighbors: bool = True):  # pragma: no cover
        return WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id=aid),
            events=[],
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            allowed_ids=[aid],
            snapshot_etag="unknown",
        )

    # The global evidence builder instance lives in gateway.app
    monkeypatch.setattr(
        gw_app._evidence_builder, "build", _stub_build.__get__(gw_app._evidence_builder, type(gw_app._evidence_builder)),
        raising=True,
    )

    # Patch the intent router to return a mixture of neighbour types
    async def _stub_route_query(question: str, functions):  # pragma: no cover
        neighbours = [
            {"type": "event", "id": "e1"},
            {"type": "decision", "id": "d2"},
            # Transition pointing *to* the anchor ⇒ preceding
            {"type": "transition", "id": "t_pre", "to": anchor_id, "from": "other"},
            # Transition pointing *from* the anchor ⇒ succeeding
            {"type": "transition", "id": "t_suc", "from": anchor_id, "to": "other2"},
        ]
        return {
            "function_calls": functions,
            "routing_confidence": 1.0,
            "routing_model_id": "router_stub_test",
            "results": {
                "get_graph_neighbors": {"neighbors": neighbours},
                # search_similar omitted for clarity
            },
        }

    monkeypatch.setattr("gateway.intent_router.route_query", _stub_route_query, raising=True)

    # Patch the builder to bypass LLM and return the evidence unchanged
    from core_models.models import WhyDecisionAnswer, WhyDecisionResponse, CompletenessFlags

    async def _stub_build_why_decision_response(req, builder):  # pragma: no cover
        # Use the evidence passed in the AskIn request directly
        ans = WhyDecisionAnswer(
            short_answer="stub",
            supporting_ids=req.evidence.allowed_ids,
        )
        resp = WhyDecisionResponse(
            intent=req.intent,
            evidence=req.evidence,
            answer=ans,
            completeness_flags=CompletenessFlags(),
            meta={},
        )
        return resp, {}, "testreq"

    monkeypatch.setattr("gateway.builder.build_why_decision_response", _stub_build_why_decision_response, raising=True)

    # Use TestClient to exercise the HTTP endpoint; default functions include get_graph_neighbors
    client = TestClient(gw_app.app)
    response = client.post("/v2/query", json={"text": "Why?"})
    assert response.status_code == 200, response.text
    body = response.json()
    # The response should contain a WhyDecisionResponse shape
    assert "evidence" in body, "expected evidence in response"
    evidence = body["evidence"]
    # We expect ONLY true events in evidence.events (decision neighbours are dropped)
    events = evidence.get("events") or []
    ids_in_events = {e.get("id") for e in events}
    assert ids_in_events == {"e1"}, f"non-event leaked into events: {ids_in_events}"

    # Transition neighbours must be split by direction and included
    trans = evidence.get("transitions") or {}
    pre_ids = {t.get("id") for t in (trans.get("preceding") or [])}
    suc_ids = {t.get("id") for t in (trans.get("succeeding") or [])}
    assert pre_ids == {"t_pre"}, f"preceding transitions wrong: {pre_ids}"
    assert suc_ids == {"t_suc"}, f"succeeding transitions wrong: {suc_ids}"

    # allowed_ids must equal {anchor} ∪ events ∪ present transitions (no decisions)
    allowed = set(evidence.get("allowed_ids") or [])
    assert allowed == {anchor_id, "e1", "t_pre", "t_suc"}, f"allowed_ids mismatch: {allowed}"
    # Transitions should be split into preceding and succeeding
    transitions = evidence.get("transitions") or {}
    pre = transitions.get("preceding") or []
    suc = transitions.get("succeeding") or []
    assert {t.get("id") for t in pre} == {"t_pre"}, f"preceding transitions incorrect: {pre}"
    assert {t.get("id") for t in suc} == {"t_suc"}, f"succeeding transitions incorrect: {suc}"
    # allowed_ids should include anchor and all neighbour IDs
    allowed = set(evidence.get("allowed_ids") or [])
    expected_ids = {anchor_id, "e1", "t_pre", "t_suc"}
    assert allowed == expected_ids, f"allowed_ids mismatch: {allowed}"