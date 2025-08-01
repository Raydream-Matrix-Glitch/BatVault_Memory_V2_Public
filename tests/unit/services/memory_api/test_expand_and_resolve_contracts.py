import httpx

BASE = "http://memory_api:8000"

def test_expand_candidates_contract():
    # Without data, should still return shape with neighbors list
    r = httpx.post(f"{BASE}/api/graph/expand_candidates",
                   json={"anchor": "nonexistent", "k": 1}, timeout=3.0)
    assert r.status_code == 200
    body = r.json()
    assert "anchor" in body and "neighbors" in body
    assert isinstance(body["neighbors"], list)

def test_resolve_text_contract():
    r = httpx.post(f"{BASE}/api/resolve/text", json={"q": "test"}, timeout=3.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("query") == "test"
    assert "matches" in body
    assert isinstance(body["matches"], list)
