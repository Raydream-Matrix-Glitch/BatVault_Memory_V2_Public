# File: services/gateway/tests/test_llm_invalid_json_fallback.py

import pytest
import gateway.app as gw_app
from gateway.app import app
from fastapi.testclient import TestClient
import gateway.templater as templater
import httpx

# ────────────────────────────────────────────────────────────────
# Test stubs – avoid real network traffic & external deps
# ────────────────────────────────────────────────────────────────

class _DummyResp:
    def __init__(self, payload):
        self._json = payload
        self.headers = {}
        self.status_code = 200

    def json(self):
        return self._json

def _dummy_get(url, **kw):
    # Minimal decision envelope fixture
    return _DummyResp({
        "id": "panasonic-exit-plasma-2012",
        "supported_by": [],
        "based_on": [],
        "transitions": [],
    })

def _dummy_post(url, json=None, **kw):
    # k=1 neighbours – empty to keep bundle tiny
    return _DummyResp({
        "neighbors": {"events": [], "transitions": []},
        "meta": {"snapshot_etag": ""},
    })

# Patch sync + async httpx used inside gateway.app
gw_app.httpx.get = _dummy_get
gw_app.httpx.post = _dummy_post
gw_app.httpx.AsyncClient = lambda *a, **kw: httpx.AsyncClient(
    transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
)

# ────────────────────────────────────────────────────────────────
# The actual test
# ────────────────────────────────────────────────────────────────

def test_invalid_llm_json_triggers_fallback(monkeypatch):
    """Gateway must set meta.fallback_used=true when it repairs
    an invalid (non-conforming) LLM JSON payload."""

    # Force validate_and_fix to report 'changed'
    def _always_change(answer, allowed_ids, anchor_id):
        answer.supporting_ids = []          # wipe IDs to simulate bad JSON
        return answer, True, ["forced repair – invalid JSON"]

    monkeypatch.setattr(templater, "validate_and_fix", _always_change)

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "panasonic-exit-plasma-2012"})

    # status code must be 200
    if resp.status_code != 200:
        pytest.fail(f"unexpected status: {resp.status_code}")

    meta = resp.json().get("meta", {})
    # fallback_used must be True on repair path
    if meta.get("fallback_used") is not True:
        pytest.fail("Gateway did not flag deterministic fallback (fallback_used=True)")
