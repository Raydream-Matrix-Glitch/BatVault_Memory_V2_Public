from fastapi.testclient import TestClient
from gateway.app import app as _fastapi_app

def test_accepts_decision_ref_and_returns_canonical_anchor():
    client = TestClient(_fastapi_app)
    payload = {"intent": "why_decision", "decision_ref": "panasonic-exit-plasma-2012"}
    resp = client.post("/v2/ask", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Milestone-4 metadata must be present
    assert "prompt_fingerprint" in body["meta"]
    assert body["meta"]["latency_ms"] < 2500              # p95 budget guard
    # Gateway must return canonicalised evidence with anchor.id set to decision_ref value
    assert body.get("intent") == "why_decision"
    assert body.get("evidence", {}).get("anchor", {}).get("id") == payload["decision_ref"]