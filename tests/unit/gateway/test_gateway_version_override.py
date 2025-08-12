"""Test environment override of the gateway version in response metadata."""

import orjson
import pytest
from fastapi.testclient import TestClient

import gateway.app as gw_app
from gateway.app import app


def _stub_evidence_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the global evidence builder to return a minimal evidence bundle."""
    from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions

    async def _dummy_build(anchor_id: str):  # pragma: no cover
        return WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id=anchor_id),
            events=[],
            transitions=WhyDecisionTransitions(),
            allowed_ids=[anchor_id],
        )

    monkeypatch.setattr(
        gw_app._evidence_builder, "build", _dummy_build.__get__(gw_app._evidence_builder, type(gw_app._evidence_builder)),
        raising=True,
    )


def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch llm_client.summarise_json to return a deterministic stub."""
    import gateway.builder as gb

    def _stub_summarise_json(envelope, *args, **kwargs):  # pragma: no cover
        allowed = envelope.get("allowed_ids", []) or []
        summary = "STUB ANSWER: fallback"
        return orjson.dumps({"short_answer": summary, "supporting_ids": allowed[:1]}).decode()

    monkeypatch.setattr(gb.llm_client, "summarise_json", _stub_summarise_json, raising=True)


def _stub_templater_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch templater.validate_and_fix to no-op to simplify test."""
    import gateway.builder as gb

    def _noop(ans, allowed, anchor):  # pragma: no cover
        return ans, False, []

    monkeypatch.setattr(gb.templater, "validate_and_fix", _noop, raising=True)


def test_gateway_version_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    If the ``GATEWAY_VERSION`` environment variable is set the builder must
    propagate its value into the ``meta.gateway_version`` field.  This test
    forces a deterministic fallback path to avoid real LLM calls.
    """
    # Override gateway version via environment
    monkeypatch.setenv("GATEWAY_VERSION", "test-version-override")
    # Stub evidence builder and llm to avoid external calls
    _stub_evidence_builder(monkeypatch)
    _stub_llm(monkeypatch)
    _stub_templater_validate(monkeypatch)

    client = TestClient(app)
    resp = client.post("/v2/ask", json={"anchor_id": "test-anchor"})
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    assert meta.get("gateway_version") == "test-version-override", "gateway_version env override not respected"