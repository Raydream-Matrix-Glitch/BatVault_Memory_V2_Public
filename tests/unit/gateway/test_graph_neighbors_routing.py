"""Contract test: Gateway must call Memory-API `/api/graph/expand_candidates`
when the client explicitly asks for the *get_graph_neighbors* function.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import app


@pytest.mark.asyncio
async def test_graph_neighbors_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    async def _fake_post(self, url: str, *args, **kwargs):  # noqa: D401, PT019
        captured_urls.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    client = TestClient(app)
    client.post(
        "/v2/query",
        json={
            "text": "Expand the neighbourhood for node panasonic-exit-plasma-2012",
            "functions": ["get_graph_neighbors"],
        },
    )

    assert any(
        "graph/expand_candidates" in url for url in captured_urls
    ), "get_graph_neighbors endpoint not called"
    assert all(
        not url.endswith("/api/resolve/text") for url in captured_urls
    ), "search_similar endpoint should NOT be called"