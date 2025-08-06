# File: services/gateway/tests/test_gateway_audit_metadata.py

import pytest
from fastapi.testclient import TestClient
import gateway.app as gw_app
from gateway.app import app
import httpx

# ────────────────────────────────────────────────────────────────
# HTTP stubs (Memory-API)
# ────────────────────────────────────────────────────────────────

class _DummyResp:
    def __init__(self, payload):
        self._json = payload
        self.headers = {}
        self.status_code = 200

    def json(self):
        return self._json

# Patch gateway.httpx calls to return our dummy responses
gw_app.httpx.get = lambda *a, **k: _DummyResp({
    "id": "panasonic-exit-plasma-2012",
})
gw_app.httpx.post = lambda *a, **k: _DummyResp({
    # Milestone-3: generic flat neighbours list
    "neighbors": [],
    "meta": {"snapshot_etag": "test-etag"},
})
gw_app.httpx.AsyncClient = lambda *a, **kw: httpx.AsyncClient(
    transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
)

# ────────────────────────────────────────────────────────────────
# MinIO stub – captures artefacts in-memory
# ────────────────────────────────────────────────────────────────

class _DummyMinio:
    def __init__(self):
        self.put_calls: list[tuple[str, str, bytes]] = []

    def put_object(self, bucket, key, data, length, content_type):
        # Read all bytes so we capture the payload
        self.put_calls.append((bucket, key, data.read()))

_dummy_minio = _DummyMinio()
# Patch the minio_client factory to return our dummy
gw_app.minio_client = lambda: _dummy_minio

# ────────────────────────────────────────────────────────────────
# The actual test
# ────────────────────────────────────────────────────────────────

def test_audit_metadata_and_artefact_persistence():
    client = TestClient(app)
    response = client.post("/v2/ask", json={"anchor_id": "panasonic-exit-plasma-2012"})

    # HTTP status must be 200
    if response.status_code != 200:
        pytest.fail(f"Unexpected status code: {response.status_code}")

    # Verify audit metadata in JSON payload
    payload = response.json()
    meta = payload.get("meta", {})
    for fld in ("prompt_id", "policy_id", "prompt_fingerprint", "snapshot_etag"):
        if not meta.get(fld):
            pytest.fail(f"{fld} missing from meta")

    # On a normal path, fallback_used should be False
    if meta.get("fallback_used") is not False:
        pytest.fail("fallback_used should be False on happy-path")

    # Verify that artefacts were written to the object store
    keys = [key for _, key, _ in _dummy_minio.put_calls]
    if not any(k.endswith("envelope.json") for k in keys):
        pytest.fail("envelope.json not stored")
    if not any(k.endswith("response.json") for k in keys):
        pytest.fail("response.json not stored")
