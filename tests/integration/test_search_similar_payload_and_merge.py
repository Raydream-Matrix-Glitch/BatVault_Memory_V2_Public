"""
1.  The intent router must post the query under the canonical `q` key when
    invoking the Memory‑API text resolver.  Older implementations used
    `text`, which the Memory‑API ignores.  This regression test
    captures the payload sent by ``route_query`` and ensures it contains
    `q` and not `text`.

2.  The Gateway must merge search_similar results returned in the
    structured Memory‑API format (a dict with a ``matches`` array) into
    the evidence bundle and ``allowed_ids`` list.  Previous versions
    ignored dict results, merging only list payloads.  The second test
    exercises this by patching the intent router to return a dict
    result and asserting that the IDs are present in the final
    response.
"""

from __future__ import annotations

import pytest
import httpx  # required for monkeypatching AsyncClient
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app


@pytest.mark.asyncio
async def test_search_similar_uses_q_key(monkeypatch):
    """route_query must send the query as `q`, not `text`, to the Memory‑API."""
    calls: list[dict] = []

    class DummyResponse:
        status_code = 200
        def json(self):
            return {}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, url: str, json: dict):  # noqa: A003
            # Record each call and return a stub response
            calls.append({"url": url, "json": json})
            return DummyResponse()

    # Patch httpx.AsyncClient used in the intent router
    monkeypatch.setattr("httpx.AsyncClient", DummyClient)

    from gateway.intent_router import route_query

    await route_query("why batteries", functions=["search_similar"])

    # Find the call to the resolve endpoint
    resolve_calls = [c for c in calls if "/api/resolve/text" in c["url"]]
    assert resolve_calls, "no POST made to resolve/text endpoint"
    payload = resolve_calls[0]["json"]
    assert "q" in payload and payload["q"] == "why batteries", "query should be sent under `q` key"
    assert "text" not in payload, "payload must not include obsolete `text` key"


@pytest.mark.asyncio
async def test_search_similar_dict_results_merged(monkeypatch):
    """search_similar dict results should be merged into evidence and allowed_ids."""
    # Stub EvidenceBuilder to return a minimal bundle
    class StubEvidenceBuilder:
        async def build(self, anchor_id: str):
            from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
            ev = WhyDecisionEvidence(
                anchor=WhyDecisionAnchor(id=anchor_id),
                events=[{"id": "ev0"}],
                transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
                allowed_ids=[anchor_id, "ev0"],
            )
            ev.snapshot_etag = "stub"
            return ev

    # Patch evidence builder and LLM/templater to deterministic stubs
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())
    # Stub LLM summariser to return a valid JSON answer
    monkeypatch.setattr(
        "gateway.builder.llm_client.summarise_json",
        lambda *a, **kw: '{"short_answer": "OK", "supporting_ids": ["ev0"]}',
    )
    # Validator passes answers through unchanged
    monkeypatch.setattr(
        "gateway.builder.templater.validate_and_fix",
        lambda ans, allowed, anchor: (ans, False, []),
    )
    # Stub decision resolver to always return a known anchor
    async def stub_resolve(text: str):
        return {"id": "dec-1"}
    monkeypatch.setattr(gw_app, "resolve_decision_text", stub_resolve)

    # Patch intent router to return search_similar result as Memory‑API dict
    def stub_route_query(question: str, functions=None):
        return {
            "function_calls": ["search_similar"],
            "routing_confidence": 1.0,
            "routing_model_id": "test",
            "results": {
                "search_similar": {
                    "query": question,
                    "matches": [{"id": "evX"}, {"id": "evY"}],
                    "vector_used": False,
                }
            },
        }
    monkeypatch.setattr("gateway.intent_router.route_query", stub_route_query)

    client = TestClient(app)
    resp = client.post(
        "/v2/query",
        json={"text": "dummy", "functions": ["search_similar"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    evidence = body.get("evidence")
    # Collect event IDs; flatten dicts or use raw strings
    event_ids = [e["id"] if isinstance(e, dict) else e for e in evidence.get("events", [])]
    assert "evX" in event_ids and "evY" in event_ids, "search_similar IDs not merged into events"
    # allowed_ids should include the new neighbours
    allowed = evidence.get("allowed_ids", [])
    assert "evX" in allowed and "evY" in allowed, "search_similar IDs not present in allowed_ids"