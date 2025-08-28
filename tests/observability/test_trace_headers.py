from fastapi.testclient import TestClient
from services.gateway.src.gateway.app import app as gw_app

def test_gateway_sets_x_trace_id_header():
    client = TestClient(gw_app)
    r = client.post("/v2/ask?stream=false", json={"intent":"why_decision","decision_ref":"philips-exit-tv-2011"})
    # We don't assert full payloads here; just header presence to catch regressions
    assert "x-trace-id" in r.headers
    assert r.headers["x-trace-id"] != ""