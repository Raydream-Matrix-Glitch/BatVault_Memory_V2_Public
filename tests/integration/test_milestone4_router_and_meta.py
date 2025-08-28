"""
Additional integration tests covering Milestone‑4 routing behaviour and meta
field propagation.  These tests are designed to close coverage gaps around
evidence merging and intent‑router contracts.  They are expected to fail
against the current implementation, highlighting where the Gateway needs
adjustments to meet the project milestones.

The key behaviours under test are:

* When functions are supplied to the `/v2/query` endpoint, the Gateway
  should call the appropriate Memory‑API helpers via the intent router.
  It should merge any returned IDs into the evidence bundle and update
  the `allowed_ids` list accordingly (roadmap §M4).
* The Gateway must expose routing metadata (`function_calls`,
  `routing_confidence`, `routing_model_id`) in the final response
  `meta` block rather than discarding it (roadmap §M4; tech‑spec §B1).
* The `get_graph_neighbors` helper should post a `node_id` to the graph
  expansion endpoint rather than echoing the raw question text.  This
  aligns the contract with the graph helper definition in the tech
  specification.

Each test uses `monkeypatch` to isolate the behaviour under test by
patching out network calls and expensive components.  Simple stub
classes replace the evidence builder and LLM summariser to keep the
focus on routing and meta propagation.
"""

import asyncio
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app


class StubEvidenceBuilder:
    """Minimal evidence builder returning a deterministic bundle.

    The stub returns a single anchor event and a known list of events.
    `allowed_ids` is initialised with the anchor and the base event so
    that merging new neighbours can be observed.
    """

    async def build(self, anchor_id: str):
        from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
        ev = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id=anchor_id),
            events=[{"id": "ev0"}],
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            allowed_ids=[anchor_id, "ev0"],
        )
        # mimic snapshot etag so fingerprinting works downstream
        ev.snapshot_etag = "stub-etag"
        return ev


def stub_llm_json(*args, **kwargs) -> str:
    """Return a simple JSON answer for the LLM summariser stub.

    The returned object conforms to the ``WhyDecisionAnswer`` schema.
    This avoids triggering the fallback templater or validator repairs.
    """
    # Note: use standard double‑quoted JSON here; pyproject rejects
    # backslash escapes within f‑strings, so keep it simple.
    return '{"short_answer": "Because...", "supporting_ids": ["dec-1"]}'


def stub_validate_and_fix(ans, allowed_ids, anchor_id):
    """Identity validator stub.

    Returns the answer unchanged and signals no schema change or errors.
    """
    return ans, False, []


def stub_route_query(question: str, functions: List[Any] | None = None) -> Dict[str, Any]:
    """Fake intent router returning fixed search and graph results.

    This stub mimics the shape expected by the structured logging
    contract: a list of function names, a routing confidence, a model ID
    and a `results` field containing helper payloads.  The search
    helper returns a list of event IDs and the graph helper returns a
    dictionary with a `neighbors` array of objects with `id` fields.
    """
    return {
        "function_calls": ["search_similar", "get_graph_neighbors"],
        "routing_confidence": 0.7,
        "routing_model_id": "router_test_v2",
        "results": {
            "search_similar": ["evA"],
            "get_graph_neighbors": {"neighbors": [{"id": "evB"}]},
        },
    }


@pytest.mark.asyncio
async def test_router_results_merged_into_evidence_and_meta_propagation(monkeypatch):
    """Ensure router results are merged into evidence and meta is surfaced.

    When `/v2/query` receives a `functions` list, the Gateway should
    merge any returned search or graph results into the evidence bundle.
    The `meta` block should include routing metadata.  This test
    temporarily replaces the evidence builder, LLM summariser, validator
    and resolver to eliminate side effects.
    """

    # Patch the evidence builder to a deterministic stub
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())
    # Replace the LLM summariser with a stub returning a valid JSON answer
    monkeypatch.setattr(
        "gateway.builder.llm_client.summarise_json", stub_llm_json
    )
    # Do not modify answers during validation
    monkeypatch.setattr(
        "gateway.builder.templater.validate_and_fix", stub_validate_and_fix
    )
    # Patch the intent router to return our fake results
    monkeypatch.setattr(
        "gateway.intent_router.route_query", stub_route_query
    )
    # Stub the decision resolver to always return a known anchor
    async def stub_resolve_decision_text(text: str):
        return {"id": "dec-1"}
    monkeypatch.setattr(gw_app, "resolve_decision_text", stub_resolve_decision_text)

    client = TestClient(app)
    resp = client.post(
        "/v2/query",
        json={
            "text": "Why did Panasonic exit plasma TVs?",
            "functions": [
                {"name": "search_similar"},
                {"name": "get_graph_neighbors"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    # The response should provide a `meta` block (new schema) or fallback to
    # `metadata` (legacy).  We accept either here but prefer `meta`.
    meta = body.get("meta") or body.get("metadata", {})
    assert meta, "missing meta/metadata block in response"

    # Routing metadata must surface: function_calls, routing_confidence and model_id
    assert meta.get("function_calls") == [
        "search_similar",
        "get_graph_neighbors",
    ], "function_calls not propagated in meta"
    assert meta.get("routing_confidence") == 0.7, "routing_confidence not propagated"
    assert meta.get("routing_model_id") == "router_test_v2", "routing_model_id missing"

    # Evidence should include the extra IDs returned by router results
    evidence = body["evidence"]
    # Collect event IDs for easier assertions
    event_ids = [e["id"] if isinstance(e, dict) else e.id for e in evidence["events"]]
    assert "evA" in event_ids, "search_similar ID not merged into events"
    assert "evB" in event_ids, "get_graph_neighbors ID not merged into events"

    # allowed_ids should also be extended with the new neighbours
    assert "evA" in evidence.get("allowed_ids", []), "search_similar ID not in allowed_ids"
    assert "evB" in evidence.get("allowed_ids", []), "get_graph_neighbors ID not in allowed_ids"


@pytest.mark.asyncio
async def test_graph_helper_sends_node_id(monkeypatch):
    """Verify that `get_graph_neighbors` posts a `node_id` rather than `text`.

    This test exercises the intent router directly.  It patches `httpx`
    so that no real HTTP calls are made and captures the payload sent
    to the Memory‑API graph helper.  The payload should include
    `node_id` equal to the query argument and must not include the
    obsolete `text` key.
    """

    # Capture payloads sent to AsyncClient.post
    calls: List[Dict[str, Any]] = []

    class DummyResponse:
        status_code = 200
        def json(self):
            # return minimal shape expected by route_query stub
            return {"neighbors": []}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, url: str, json: Dict[str, Any]):  # noqa: A003
            # Record each call and return a stub response
            calls.append({"url": url, "json": json})
            return DummyResponse()

    # Patch httpx.AsyncClient with our dummy client
    monkeypatch.setattr("httpx.AsyncClient", DummyClient)

    # Invoke route_query asynchronously
    from gateway.intent_router import route_query
    await route_query("node-123", functions=[{"name": "get_graph_neighbors"}])

    # Find the call to graph expand
    expand_calls = [c for c in calls if "/api/graph/expand_candidates" in c["url"]]
    assert expand_calls, "no call made to graph expand endpoint"
    payload = expand_calls[0]["json"]
    assert "node_id" in payload and payload["node_id"] == "node-123", (
        "get_graph_neighbors should post node_id but did not"
    )
    assert "text" not in payload, "graph expand payload should not include 'text'"
