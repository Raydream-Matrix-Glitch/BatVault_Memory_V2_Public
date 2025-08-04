import pytest
from fastapi.testclient import TestClient
from services.api_edge.src.api_edge import app as app_module

client = TestClient(app_module.app)

# ---------------------------------------------------------------
# Auth
# ---------------------------------------------------------------
def test_unauthenticated_request_returns_401(monkeypatch):
    # Enable auth for this test only
    monkeypatch.setattr(app_module.settings, "auth_disabled", False)
    r = client.get("/healthz")
    assert r.status_code == 401

def test_bearer_token_allows_request(monkeypatch):
    monkeypatch.setattr(app_module.settings, "auth_disabled", False)
    r = client.get("/healthz", headers={"Authorization": "Bearer testtoken"})
    assert r.status_code == 200

# ---------------------------------------------------------------
# CORS
# ---------------------------------------------------------------
def test_cors_preflight_allows_frontend_origin():
    r = client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
