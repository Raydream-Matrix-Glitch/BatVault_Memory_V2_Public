"""Test fallback behaviour of `/v2/query` when no anchor can be resolved.

This test exercises the scenario where the decision resolver returns
``None`` for a given query.  In such cases the Gateway should
degrade gracefully instead of raising HTTP 404.  When the offline
BM25 search yields at least one match the top match's identifier
should be promoted to serve as the anchor and a full
WhyDecisionResponse must be returned.  When there are no matches
the provisional Milestone‑3 shape with an empty ``matches`` list
should be returned.
"""

import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app


@pytest.mark.asyncio
async def test_query_promotes_bm25_match_when_resolver_returns_none(monkeypatch):
    """If the resolver returns ``None`` but BM25 returns matches, the top
    match should be used as the anchor so that a WhyDecisionResponse
    is produced (HTTP 200)."""

    # Force the resolver to return None for any input
    async def _resolver_none(text: str):
        return None
    monkeypatch.setattr(gw_app, "resolve_decision_text", _resolver_none)

    # Stub BM25 to return a single candidate ID
    async def _search_one(text: str, k: int = 24):
        return [{"id": "abc123", "score": 1, "match_snippet": "stub"}]

    # Patch the search function used by the resolver fallback
    monkeypatch.setattr("gateway.resolver.fallback_search.search_bm25", _search_one, raising=False)

    client = TestClient(app)
    resp = client.post("/v2/query", json={"text": "nonexistent"})
    assert resp.status_code == 200, "Query fallback with BM25 match did not return 200"
    body = resp.json()
    # When a match exists the Gateway should return a full WhyDecisionResponse
    assert set(["answer", "evidence", "meta"]).issubset(body.keys()), (
        "Expected WhyDecisionResponse keys when promoting BM25 match"
    )


@pytest.mark.asyncio
async def test_query_returns_empty_matches_when_no_candidates(monkeypatch):
    """When both the resolver and BM25 return no results, the endpoint
    should return a 200 response with an empty ``matches`` list."""

    # Resolver returns None regardless of input
    async def _resolver_none(text: str):
        return None
    monkeypatch.setattr(gw_app, "resolve_decision_text", _resolver_none)

    # BM25 search yields no candidates
    async def _search_none(text: str, k: int = 24):
        return []
    monkeypatch.setattr("gateway.resolver.fallback_search.search_bm25", _search_none, raising=False)

    client = TestClient(app)
    resp = client.post("/v2/query", json={"text": "nothing"})
    assert resp.status_code == 200, "Query fallback with no matches did not return 200"
    body = resp.json()
    assert "matches" in body, (
        "Expected provisional matches array when no results available"
    )
    assert body.get("matches", []) == [], "matches list should be empty when no candidates"