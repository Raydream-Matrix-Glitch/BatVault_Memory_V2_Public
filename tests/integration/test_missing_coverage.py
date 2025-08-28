"""
Integration tests that close the Milestone-3 coverage gaps:

  1.  Retry logic – verify the gateway records **exactly two** retries
     before templater fallback after schema errors.
  2.  SSE streaming – basic contract on chunk boundaries and event format.
  3.  Observability / metrics – evidence-bundle metrics present & sane.
  4.  S3/MinIO integration – content-addressable writes and ETag integrity.
  5.  `ensure_bucket` helper – idempotent bucket creation semantics.

All tests use *real* fixture data from `memory/fixtures/…` and patch
external deps (Redis, HTTP, MinIO) so they run fully offline.
"""

import glob
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app                            # FastAPI instance
from api_edge.app import app as edge_app               # SSE endpoint lives here
from core_models.models import (
    WhyDecisionEvidence,
    WhyDecisionAnchor,
    WhyDecisionTransitions,
)
from core_storage.minio_utils import ensure_bucket

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _load_fixture_events(max_n: int = 5):
    """Load *real* event documents from memory/fixtures for realistic bundles."""
    root = Path(__file__).resolve().parents[2]  # repo root
    pattern = root / "memory" / "fixtures" / "events" / "*.json"
    evts = []
    for p in sorted(glob.glob(str(pattern)))[:max_n]:
        with open(p) as fh:
            evts.append(json.load(fh))
    return evts


class StubEvidenceBuilder:
    """EvidenceBuilder replacement: returns deterministic, budget-safe bundles."""

    def __init__(self):
        self._events = _load_fixture_events()

    async def build(self, anchor_id: str):
        evidence = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id=anchor_id),
            events=self._events,
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            allowed_ids=[anchor_id] + [e["id"] for e in self._events],
            supporting_ids=[anchor_id],
        )
        # mimic ingest fingerprint so downstream contract is intact
        evidence.snapshot_etag = "sha256:dummytag"
        return evidence


# --------------------------------------------------------------------------- #
#  Fake OpenAI client to track retry attempts                                 #
# --------------------------------------------------------------------------- #
import os, sys, types

class _FakeChatCompletion:
    calls = 0

    @classmethod
    def create(cls, *args, **kwargs):
        cls.calls += 1
        raise RuntimeError("forced failure – stubbed by test")

fake_openai = types.ModuleType("openai")
fake_openai.ChatCompletion = _FakeChatCompletion
fake_openai.api_key = "test"
sys.modules["openai"] = fake_openai

# Ensure the Gateway uses the real path (not its own stub)
os.environ["OPENAI_DISABLED"] = "0"


class StubMinio:
    """Tiny in-memory stand-in for MinIO; tracks blobs & returns MD5 ETags."""

    def __init__(self):
        self.objects = {}

    # bucket helpers --------------------------------------------------------
    def bucket_exists(self, bucket):          # noqa: D401
        return bucket in self.objects

    def make_bucket(self, bucket):
        self.objects[bucket] = {}

    def get_bucket_lifecycle(self, bucket):   # pragma: no cover
        return None

    def set_bucket_lifecycle(self, bucket, lifecycle):  # pragma: no cover
        pass

    # object helpers --------------------------------------------------------
    class _PutResult:
        def __init__(self, etag):
            self.etag = etag

    def put_object(  # noqa: D401
        self,
        bucket,
        object_name,
        data_stream,
        length,
        content_type="application/json",
    ):
        blob = data_stream.read()
        etag = hashlib.md5(blob).hexdigest()
        self.objects.setdefault(bucket, {})[object_name] = {"blob": blob, "etag": etag}
        return self._PutResult(etag)


# ────────────────────────────────────────────────────────────────────────────
# 1. Retry Logic
# ────────────────────────────────────────────────────────────────────────────


def test_llm_retry_exactly_two(monkeypatch):
    """
    Gateway must attempt the LLM no more than **two** times after the first
    failure.  We count calls to ``openai.ChatCompletion.create`` and ensure
    ``meta.retries`` reflects those retries.
    """

    # short-circuit expensive stages
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())

    client = TestClient(app)
    resp = client.post(
        "/v2/ask",
        json={"anchor_id": "panasonic-exit-plasma-2012"},
    )
    assert resp.status_code == 200
    meta = resp.json()["meta"]

    assert meta["retries"] == 2, "Gateway did not record exactly two retries"
    assert _FakeChatCompletion.calls == meta["retries"] + 1


# ────────────────────────────────────────────────────────────────────────────
# 2. SSE Streaming
# ────────────────────────────────────────────────────────────────────────────


def test_sse_stream_chunk_boundaries():
    """Verify `\\n\\n` chunk delimiters and event prefix in SSE demo."""

    edge_client = TestClient(edge_app)
    resp = edge_client.get(
        "/stream/demo", headers={"Accept": "text/event-stream"}
    )
    assert resp.status_code == 200

    payload = resp.content.decode()
    parts = [p for p in payload.split("\n\n") if p.strip()]
    assert len(parts) >= 5, "expected ≥5 SSE chunks"
    assert all(p.startswith("event: tick") for p in parts)


# ────────────────────────────────────────────────────────────────────────────
# 3. Observability / Metrics
# ────────────────────────────────────────────────────────────────────────────


def test_evidence_metrics_presence(monkeypatch):
    """`meta.evidence_metrics` must include core counters."""

    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())

    client = TestClient(app)
    resp = client.post(
        "/v2/ask",
        json={"anchor_id": "panasonic-exit-plasma-2012"},
    )
    meta = resp.json()["meta"]
    metrics = meta.get("evidence_metrics")

    assert metrics, "evidence_metrics missing"
    for k in ("total_neighbors_found", "selector_truncation", "final_evidence_count"):
        assert k in metrics


# ────────────────────────────────────────────────────────────────────────────
# 4. S3 / MinIO Integration
# ────────────────────────────────────────────────────────────────────────────


def test_minio_content_addressable(monkeypatch):
    """Gateway must write artifacts under request-scoped prefix with MD5 ETags."""

    stub_minio = StubMinio()
    monkeypatch.setattr(gw_app, "minio_client", lambda: stub_minio)
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())

    client = TestClient(app)
    client.post(
        "/v2/ask",
        json={"anchor_id": "panasonic-exit-plasma-2012", "request_id": "req-123"},
    )

    bucket_objs = stub_minio.objects.get(gw_app.settings.minio_bucket, {})
    assert any(
        name.startswith("req-123/") for name in bucket_objs
    ), "objects not written with request prefix"

    # every stored object’s etag must equal its MD5 digest
    for obj in bucket_objs.values():
        blob = obj["blob"]
        assert obj["etag"] == hashlib.md5(blob).hexdigest()


# ────────────────────────────────────────────────────────────────────────────
# 5. ensure_bucket helper – idempotency
# ────────────────────────────────────────────────────────────────────────────


def test_ensure_bucket_idempotent():
    stub = StubMinio()

    first = ensure_bucket(stub, bucket="tests", retention_days=7)
    assert first["newly_created"] is True

    second = ensure_bucket(stub, bucket="tests", retention_days=7)
    assert second["newly_created"] is False