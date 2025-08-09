"""Contract test: Gateway → Memory-API call-out.

Milestone-4 requires that when the client supplies
``"functions": ["search_similar","get_graph_neighbors"]`` the Gateway’s
intent-router must POST to **both** Memory-API endpoints:

  • `/api/resolve/text`              – for *search_similar*
  • `/api/graph/expand_candidates`   – for *get_graph_neighbors*

The feature is **not** wired up yet, so this test is intentionally
strict and should fail until the integration lands.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import app


@pytest.mark.asyncio
async def test_memory_api_calls_for_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spy on ``httpx.AsyncClient.post`` and verify both Memory-API URLs."""

    captured_urls: list[str] = []

    # --- monkey-patch --------------------------------------------------
    async def _fake_post(self, url: str, *args, **kwargs):  # type: ignore[no-self-use]
        captured_urls.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    # --- exercise system under test ------------------------------------
    client = TestClient(app)
    client.post(
        "/v2/query",
        json={
            "text": "Why did Panasonic exit plasma TV production?",
            "functions": ["search_similar", "get_graph_neighbors"],
        },
    )

    # --- expectations --------------------------------------------------
    assert any(url.endswith("/api/resolve/text") for url in captured_urls), "search_similar endpoint not called"
    assert any("graph/expand_candidates" in url for url in captured_urls), "get_graph_neighbors endpoint not called"