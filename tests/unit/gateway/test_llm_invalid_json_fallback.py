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
    """Stub for gateway.app.httpx.get: return only the anchor decision envelope."""
    return _DummyResp({"id": "panasonic-exit-plasma-2012"})

def _dummy_post(url, json=None, **kw):
    """Stub for gateway.app.httpx.post: k=1 neighbours – empty to keep bundle tiny."""
    return _DummyResp({
        "neighbors": [],
        "meta": {"snapshot_etag": ""},
    })

# Patch sync + async httpx used inside gateway.app
gw_app.httpx.get = _dummy_get
gw_app.httpx.post = _dummy_post
# Preserve kwargs such as ``base_url`` while forcing an in-memory transport
_orig_async_client = httpx.AsyncClient

def _mock_async_client(*args, **kwargs):
    kwargs.setdefault(
        "transport",
        httpx.MockTransport(lambda _req: httpx.Response(200, json={})),
    )
    return _orig_async_client(*args, **kwargs)

gw_app.httpx.AsyncClient = _mock_async_client

# ────────────────────────────────────────────────────────────────
# The actual test
# ────────────────────────────────────────────────────────────────

def test_invalid_llm_json_triggers_fallback(monkeypatch):
    """Gateway must set meta.fallback_used=true when it repairs
    an invalid (non-conforming) LLM JSON payload."""

    # Stub the core validator to always emit at least one error.  The
    # boolean component of the return value is ignored by the builder; a
    # non‑empty errors list triggers fallback_used=True.
    monkeypatch.setattr(
        "gateway.builder.validate_response",
        lambda _resp: (True, [{"code": "forced_repair", "details": {"reason": "invalid JSON"}}]),
        raising=False,   # builder doesn't export this by default
    )

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "panasonic-exit-plasma-2012"})

    # status code must be 200
    if resp.status_code != 200:
        pytest.fail(f"unexpected status: {resp.status_code}")

    meta = resp.json().get("meta", {})
    # fallback_used must be True on repair path
    if meta.get("fallback_used") is not True:
        pytest.fail("Gateway did not flag deterministic fallback (fallback_used=True)")
