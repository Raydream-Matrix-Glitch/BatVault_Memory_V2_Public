"""Contract test: Gateway must call Memory-API `/api/resolve/text`
when the client explicitly asks for the *search_similar* function.

The test spies on `httpx.AsyncClient.post` to capture outbound
requests.  It passes *only* ``"functions": ["search_similar"]``
and then checks that exactly the resolver endpoint is hit.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import app


@pytest.mark.asyncio
async def test_search_similar_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    # ---- monkey-patch ----------------------------------------------------
    async def _fake_post(self, url: str, *args, **kwargs):  # noqa: D401, PT019
        captured_urls.append(url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    # ---- exercise --------------------------------------------------------
    client = TestClient(app)
    client.post(
        "/v2/query",
        json={
            "text": "Show me similar decisions about batteries.",
            "functions": ["search_similar"],
        },
    )

    # ---- assertions ------------------------------------------------------
    assert any(
        url.endswith("/api/resolve/text") for url in captured_urls
    ), "search_similar endpoint not called"
    assert all(
        "graph/expand_candidates" not in url for url in captured_urls
    ), "get_graph_neighbors endpoint should NOT be called"
