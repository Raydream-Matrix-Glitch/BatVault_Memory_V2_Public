# tests/unit/services/ingest/test_ingest_health.py

from fastapi.testclient import TestClient
from services.ingest.src.ingest.app import app

client = TestClient(app)


def test_healthz_liveness():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_readyz_starting():
    # Ensure snapshot_etag is unset
    if hasattr(app.state, "snapshot_etag"):
        delattr(app.state, "snapshot_etag")

    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json()["status"] == "starting"


def test_readyz_ready(monkeypatch):
    # Simulate snapshot loaded
    app.state.snapshot_etag = "sha256:test"
    # Stub out Memory-API check
    monkeypatch.setattr(
        "services.ingest.src.ingest.app._ping_gateway_ready",
        lambda: True,
    )

    res = client.get("/readyz")
    body = res.json()

    assert res.status_code == 200
    assert body["status"] == "ready"
    assert body["snapshot_etag"] == "sha256:test"
    assert "request_id" in body
