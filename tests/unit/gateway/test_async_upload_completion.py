"""
Regression test for artefact upload completion.

When ``/v2/ask`` is called the Gateway writes a number of artefacts to the
object store.  Previously these writes were scheduled via ``asyncio.create_task``
which allowed the request to finish before the uploads completed.  In a
multi‑test run this caused artefacts from an earlier test to leak into the
next test.  This test issues two back‑to‑back requests and verifies that
all expected artefacts are present immediately after each call.  The
``ask`` handler now awaits the upload coroutine directly, ensuring
deterministic behaviour across tests.
"""

from fastapi.testclient import TestClient
import httpx
import gateway.app as gw_app


class _DummyResp:
    """Simple HTTPX response stub with JSON payload."""
    def __init__(self, payload):
        self._json = payload
        self.headers = {}
        self.status_code = 200
    def json(self):  # noqa: D401
        return self._json


class _DummyMinio:
    """In-memory MinIO stand‑in recording artefact uploads."""
    def __init__(self):
        self.put_calls: list[tuple[str, str, bytes]] = []
    def put_object(self, bucket, key, data, length, content_type):
        self.put_calls.append((bucket, key, data.read()))


def test_upload_completes_before_response(monkeypatch):
    """
    Perform two sequential ``/v2/ask`` requests and assert that artefacts are
    persisted synchronously for each call.
    """
    # Patch Memory‑API endpoints to avoid external HTTP calls
    monkeypatch.setattr(
        gw_app.httpx,
        "get",
        lambda *a, **k: _DummyResp({"id": "stub"}),
        raising=False,
    )
    monkeypatch.setattr(
        gw_app.httpx,
        "post",
        lambda *a, **k: _DummyResp({"neighbors": [], "meta": {"snapshot_etag": "e"}}),
        raising=False,
    )

    # Ensure AsyncClient uses an in-memory transport
    _real_async = httpx.AsyncClient
    def _dummy_async_client(*args, **kwargs):
        kwargs.setdefault(
            "transport",
            httpx.MockTransport(lambda _req: httpx.Response(200, json={})),
        )
        return _real_async(*args, **kwargs)
    monkeypatch.setattr(gw_app.httpx, "AsyncClient", _dummy_async_client, raising=False)

    # Replace MinIO client with our dummy
    dummy_minio = _DummyMinio()
    monkeypatch.setattr(gw_app, "minio_client", lambda: dummy_minio, raising=True)

    client = TestClient(gw_app.app)
    expected_suffixes = {
        "envelope.json",
        "rendered_prompt.txt",
        "llm_raw.json",
        "validator_report.json",
        "response.json",
        "evidence_pre.json",
        "evidence_post.json",
    }
    # Call twice to detect any cross‑call interference
    for i in range(2):
        resp = client.post(
            "/v2/ask",
            json={"intent": "why_decision", "decision_ref": f"id-{i}"},
        )
        assert resp.status_code == 200, f"unexpected status {resp.status_code}"
        suffixes = {key.split("/", 1)[-1] for _, key, _ in dummy_minio.put_calls}
        missing = expected_suffixes - suffixes
        assert not missing, f"Missing artefacts on call {i}: {missing}"
        dummy_minio.put_calls.clear()