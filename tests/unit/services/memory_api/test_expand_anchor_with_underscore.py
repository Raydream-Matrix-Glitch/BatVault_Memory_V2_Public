import pytest
from fastapi.testclient import TestClient

from memory_api.app import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_expand_anchor_with_underscore(client):
    """ID regex allows underscores; endpoint must round-trip such IDs."""
    payload = {"anchor": "foo_bar_baz", "k": 1}
    r = client.post("/api/graph/expand_candidates", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["anchor"] == "foo_bar_baz"
    assert "neighbors" in body and isinstance(body["neighbors"], list)