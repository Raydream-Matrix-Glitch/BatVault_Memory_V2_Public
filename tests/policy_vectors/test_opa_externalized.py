import json
import types
import httpx
import pytest
from core_policy_opa.adapter import opa_decide_if_enabled

class _Resp:
    def __init__(self, payload, status_code=200):
        self._p = payload
        self.status_code = status_code
    def raise_for_status(self): 
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)
    def json(self): 
        return self._p

class _Client:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def post(self, url, json=None):
        return _Resp({"result": {
            "allowed_ids": ["b#2","a#1","a#1"],  # dedupe, sort -> ["a#1","b#2"]
            "extra_visible": ["*"],
            "policy_fingerprint": "sha256:deadbeef",
        }})

@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    class S:
        opa_url = "http://opa:8181"
        opa_decision_path = "/v1/data/batvault/decision"
        opa_timeout_ms = 500
        opa_bundle_sha = None
    monkeypatch.setattr("core_policy_opa.adapter.get_settings", lambda: S())
    yield

def test_opa_decide_happy(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _Client)
    out = opa_decide_if_enabled(
        anchor_id="a#1",
        edges=[{"type": "LED_TO", "from": "a#1", "to": "b#2", "timestamp": "2025-01-01T00:00:00Z"}],
        headers={"x-extra-allow":"*"},
        snapshot_etag="etag",
    )
    assert out is not None
    assert out.allowed_ids == ["a#1","b#2"]
    assert out.extra_visible == ["*"]
    assert out.policy_fp == "sha256:deadbeef"

def test_opa_decide_network_fallback(monkeypatch):
    class _Broken(httpx.Client):
        def post(self, *a, **k): 
            raise httpx.ConnectError("boom", request=None)
    monkeypatch.setattr(httpx, "Client", _Broken)
    out = opa_decide_if_enabled(
        anchor_id="a#1", edges=[], headers={}, snapshot_etag="e")
    assert out is None