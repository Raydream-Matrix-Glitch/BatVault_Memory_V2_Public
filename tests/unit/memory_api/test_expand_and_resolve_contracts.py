import os, httpx

# The _memory_api_server fixture publishes the actual base URL via
# an env-var when it has to fall back to a random free port.
BASE = os.getenv("MEMORY_API_BASE", "http://memory_api:8000")

def test_expand_candidates_contract():
    # Without data, should still return shape with neighbors list
    r = httpx.post(f"{BASE}/api/graph/expand_candidates",
                   json={"node_id": "nonexistent", "k": 1}, timeout=3.0)
    assert r.status_code == 200
    body = r.json()
    assert "node_id" in body and "neighbors" in body
    assert isinstance(body["neighbors"], list)

def test_resolve_text_contract():
    r = httpx.post(f"{BASE}/api/resolve/text", json={"q": "test"}, timeout=3.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("query") == "test"
    assert "matches" in body
    assert isinstance(body["matches"], list)
