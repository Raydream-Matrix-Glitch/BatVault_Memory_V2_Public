# File: tests/unit/services/gateway/test_artifact_retention_comprehensive.py
"""
Extends the earlier audit-metadata unit test to verify **all** artefacts
enumerated in §10.3 of the Core Spec are persisted for every request (R3.3).
The test uses a MinIO stub identical to the one in the existing
`test_gateway_audit_metadata.py` file, but checks for the full artefact set.
"""
import importlib, types
from fastapi.testclient import TestClient
import pytest, httpx, gateway.app as gw_app
from gateway.app import app as _fastapi_app

# ──────────────────────────────────────────────
# HTTP stubs  (Memory-API endpoints)
# ──────────────────────────────────────────────
class _DummyResp:
    def __init__(self, payload):
        self._json = payload
        self.headers = {}
        self.status_code = 200
    def json(self):  return self._json

gw_app.httpx.get  = lambda *a, **k: _DummyResp(
    {"id": "panasonic-exit-plasma-2012"}
)
gw_app.httpx.post = lambda *a, **k: _DummyResp(
    {"neighbors": [], "meta": {"snapshot_etag": "test-etag"}}
)
# safeguard against self-calling lambda
_orig_async_client = httpx.AsyncClient

def _mock_async_client(*args, **kwargs):
    kwargs.setdefault(
        "transport",
        httpx.MockTransport(lambda _req: httpx.Response(200, json={})),
    )
    return _orig_async_client(*args, **kwargs)

gw_app.httpx.AsyncClient = _mock_async_client
# ──────────────────────────────────────────────
# MinIO stub  (captures artefact writes in-memory)
# ──────────────────────────────────────────────
class _DummyMinio:
    def __init__(self):
        self.put_calls = []   # (bucket, key, bytes)
    def put_object(self, bucket, key, data, length, content_type):
        self.put_calls.append((bucket, key, data.read()))

_dummy_minio = _DummyMinio()
gw_app.minio_client = lambda: _dummy_minio          # monkey-patch

# Expected artefact file names  (from spec §10.3)
_EXPECTED_SUFFIXES = {
    "envelope.json",
    "rendered_prompt.txt",
    "llm_raw.json",
    "validator_report.json",
    "response.json",
    "evidence_pre.json",
    "evidence_post.json",
}

# ──────────────────────────────────────────────
# The actual test
# ──────────────────────────────────────────────
def test_full_artefact_retention():
    client = TestClient(_fastapi_app)

    # Perform a happy-path /v2/ask request (templater path – no LLM needed)
    resp = client.post("/v2/ask", json={"intent": "why_decision", "decision_ref": "panasonic-exit-plasma-2012"})
    if resp.status_code != 200:
        # Diagnostic: expose declared contract & validation failure
        print("OpenAPI entry for /v2/ask:", client.get("/openapi.json").json()["paths"]["/v2/ask"])
        print("Validation error detail:", resp.json())
    assert resp.status_code == 200, f"unexpected status; body: {resp.text}"

    # Collect artefact keys written by gateway
    keys = {key.split("/", 1)[-1] for _, key, _ in _dummy_minio.put_calls}

    missing = _EXPECTED_SUFFIXES - keys
    assert not missing, f"Missing artefacts: {', '.join(sorted(missing))}"
