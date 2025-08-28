"""
Integration tests for load‑shedding behaviour and comprehensive meta
field coverage.  These tests complement the existing suite by
verifying two scenarios that are critical for Milestone 4 and Milestone 5:

1. The Gateway should set the `meta.load_shed` flag when the system
   determines it is overloaded.  For the `/v2/query` endpoint, an
   overloaded state should result in an HTTP 429 with a retry header;
   for `/v2/ask`, the call should still succeed but the meta flag must
   be present.  Both cases are covered here.
2. The `meta` block returned by the Gateway must expose a complete set
   of telemetry fields: policy identifiers, prompt fingerprints,
   selector metrics, retry counts and so on.  This test enumerates
   the required keys and checks that they exist in the response for
   `/v2/ask` when run with deterministic stubs.

These tests use monkeypatching to isolate the Gateway from external
dependencies such as Redis, HTTP and the LLM.  A stub evidence
builder and summariser ensure that only the meta logic is exercised.
"""

import pytest
import orjson
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app


class StubEvidenceBuilder:
    """Return a trivial evidence bundle for load‑shed and meta tests."""
    async def build(self, anchor_id: str):
        from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions

        ev = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id=anchor_id),
            events=[{"id": "ev0", "type": "event", "x-extra": {}}],
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            allowed_ids=[anchor_id, "ev0"],
        )
        ev.snapshot_etag = "stub-etag"
        return ev


def stub_llm_json(*args, **kwargs) -> str:
    """
    Return a valid JSON answer for the LLM stub.

    The first positional argument is expected to be the prompt envelope
    dictionary.  We inspect its ``allowed_ids`` to extract the anchor
    identifier (the first element after the builder’s deterministic
    ordering), and we return it as the sole supporting_id.  This avoids
    triggering validator repairs for missing anchors.
    """
    anchor_id: str | None = None
    if args:
        env = args[0]
        if isinstance(env, dict):
            allowed_ids = env.get("allowed_ids") or []
            if allowed_ids:
                anchor_id = allowed_ids[0]
    if anchor_id is None:
        anchor_id = "unknown"
    return orjson.dumps({"short_answer": "All good", "supporting_ids": [anchor_id]}).decode()


def stub_validate_and_fix(ans, allowed_ids, anchor_id):
    return ans, False, []


@pytest.mark.asyncio
async def test_load_shed_flag(monkeypatch):
    """Load‑shed flag should appear in meta and trigger HTTP 429 on query."""

    # Force the system into a load‑shed state by patching should_load_shed
    monkeypatch.setattr(gw_app, "should_load_shed", lambda: True)
    monkeypatch.setattr("gateway.builder.should_load_shed", lambda: True, raising=False)
    # Replace expensive components with stubs
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())
    monkeypatch.setattr("gateway.builder.llm_client.summarise_json", stub_llm_json)
    monkeypatch.setattr("gateway.builder.templater.validate_and_fix", stub_validate_and_fix)

    client = TestClient(app)

    # 1. `/v2/query` should respond with 429 and surface meta.load_shed
    resp_q = client.post("/v2/query", json={"text": "why"})
    assert resp_q.status_code == 429, "query did not return HTTP 429 when load shedding"
    body_q = resp_q.json()
    meta_q = body_q.get("meta") or body_q.get("metadata", {})
    assert meta_q.get("load_shed") is True, "meta.load_shed flag missing for overloaded query"

    # 2. `/v2/ask` should complete but set meta.load_shed
    resp_a = client.post("/v2/ask", json={"anchor_id": "dec-1"})
    assert resp_a.status_code == 200
    meta_a = resp_a.json().get("meta", {})
    assert meta_a.get("load_shed") is True, "ask meta.load_shed not set when overloaded"


@pytest.mark.asyncio
async def test_meta_fields_complete(monkeypatch):
    """Ensure meta includes all required fields and evidence metrics."""

    # Do not load shed for this test
    monkeypatch.setattr(gw_app, "should_load_shed", lambda: False)
    monkeypatch.setattr("gateway.builder.should_load_shed", lambda: False, raising=False)
    monkeypatch.setattr(gw_app, "_evidence_builder", StubEvidenceBuilder())
    monkeypatch.setattr("gateway.builder.llm_client.summarise_json", stub_llm_json)
    monkeypatch.setattr("gateway.builder.templater.validate_and_fix", stub_validate_and_fix)

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "dec-1"})
    assert resp.status_code == 200
    meta = resp.json().get("meta")
    assert meta, "meta block missing from /v2/ask response"

    # Required top‑level meta fields per tech‑spec and roadmap
    required_fields = [
        "policy_id",
        "prompt_id",
        "prompt_fingerprint",
        "bundle_fingerprint",
        "bundle_size_bytes",
        "snapshot_etag",
        "fallback_used",
        "retries",
        "gateway_version",
        "selector_model_id",
        "latency_ms",
        "evidence_metrics",
        "load_shed",
    ]
    for field in required_fields:
        assert field in meta, f"meta missing required field: {field}"

    # Evidence metrics must include selector counters
    metrics = meta["evidence_metrics"]
    assert metrics, "evidence_metrics missing or empty"
    for k in ("total_neighbors_found", "selector_truncation", "final_evidence_count"):
        assert k in metrics, f"evidence_metrics missing key: {k}"
