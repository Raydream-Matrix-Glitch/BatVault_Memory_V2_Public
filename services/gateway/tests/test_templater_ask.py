from fastapi.testclient import TestClient
from gateway.app import app

def test_templater_returns_contract():
    c = TestClient(app)
    r = c.post("/v2/ask", json={"anchor_id":"pause-paas-rollout-2024-q3"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "why_decision"
    assert j["evidence"]["anchor"]["id"] == "pause-paas-rollout-2024-q3"
    assert j["answer"]["supporting_ids"][0] == "pause-paas-rollout-2024-q3"
    assert set(j["evidence"]["allowed_ids"]) >= set(j["answer"]["supporting_ids"])
