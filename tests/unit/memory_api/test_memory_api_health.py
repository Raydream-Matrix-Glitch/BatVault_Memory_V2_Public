# tests/unit/services/memory_api/test_memory_api_health.py

from fastapi.testclient import TestClient
from services.memory_api.src.memory_api.app import app

client = TestClient(app)


def test_healthz():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_readyz_ready(monkeypatch):
    # Stub out the internal Arango/Gateway ping so it reports healthy
    monkeypatch.setattr(
        "services.memory_api.src.memory_api.app._ping_gateway_ready",
        lambda: True,
    )

    res = client.get("/readyz")
    assert res.status_code == 200

    body = res.json()
    assert body["status"] == "ready"
    assert "request_id" in body


def test_readyz_degraded(monkeypatch):
    # Stub out the internal ping so it reports unhealthy
    monkeypatch.setattr(
        "services.memory_api.src.memory_api.app._ping_gateway_ready",
        lambda: False,
    )

    res = client.get("/readyz")
    assert res.status_code == 200

    body = res.json()
    assert body["status"] == "degraded"
    assert "request_id" in body
