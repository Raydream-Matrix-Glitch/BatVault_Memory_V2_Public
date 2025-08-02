# tests/unit/services/gateway/test_gateway_health.py

from fastapi.testclient import TestClient
from services.gateway.src.gateway.app import app

client = TestClient(app)

def test_healthz():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
