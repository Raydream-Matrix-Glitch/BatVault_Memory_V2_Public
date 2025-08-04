from fastapi.testclient import TestClient
from services.api_edge.src.api_edge.app import app

client = TestClient(app)

def test_healthz():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
