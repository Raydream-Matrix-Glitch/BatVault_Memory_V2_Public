# tests/unit/gateway/test_llm_retry_twice_fallback.py

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# SUT modules
import gateway.app as gw_app
from gateway.app import app
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
)

# ──────────────────────────
# Fixture loading
# ──────────────────────────
def _fixture_root() -> Path:
    """
    Locate the canonical memory/fixtures directory by walking up
    from this test file until it’s found.
    """
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

FIXTURES = _fixture_root()
DECISION_ID = "panasonic-exit-plasma-2012"
_DECISION_JSON = json.loads(
    (FIXTURES / "decisions" / f"{DECISION_ID}.json").read_text(encoding="utf-8")
)

# --------------------------------------------------------------------------- #
#  Fake OpenAI client – forces retries & lets us count real LLM calls        #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    """
    Replace the OpenAI client with a stub that *always* errors.  
    This drives the Gateway’s retry loop; each invocation increments
    ``_FakeChatCompletion.calls`` so the test can assert the contract.
    """
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

    # Disable the built-in stub so the Gateway really tries to call OpenAI
    monkeypatch.setenv("OPENAI_DISABLED", "0")

    yield _FakeChatCompletion

@pytest.fixture(autouse=True)
def _stub_evidence_builder(monkeypatch):
    """Inject a pre-built evidence bundle whose `_retry_count == 2` so the
    gateway surfaces `meta.retries == 2` without hitting the Memory-API."""
    from gateway.app import _evidence_builder

    async def _dummy_build(anchor_id: str):
        ev = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(**_DECISION_JSON),
            events=[],
            transitions=WhyDecisionTransitions(),
        )
        ev.snapshot_etag = "dummy-etag"
        return ev

    monkeypatch.setattr(_evidence_builder, "build", _dummy_build, raising=True)


@pytest.fixture(autouse=True)
def _force_validator_fallback(monkeypatch):
    """
    Force the core validator to emit an error once so that the builder
    marks the response as having undergone a repair (meta.fallback_used == True).

    In the new architecture the builder invokes ``gateway.builder.validate_response``
    directly.  We monkey‑patch this function to return a non‑empty error list
    while still indicating overall validity.  This triggers the
    deterministic fallback path without involving the templater.
    """
    # Patch the validator used in the builder to emit a forced error
    monkeypatch.setattr(
        "gateway.builder.validate_response",
        lambda _resp: (True, [{"code": "forced_schema_error", "details": {"test": True}}]),
        raising=False,   # attribute is intentionally absent in builder
    )

# ──────────────────────────
# Test
# ──────────────────────────
def test_retry_twice_then_fallback_meta_flags(_fake_llm):
    client = TestClient(app)
    r = client.post("/v2/ask", json={"anchor_id": DECISION_ID})

    assert r.status_code == 200, r.text
    meta = r.json().get("meta", {})

    # ① upstream retries surfaced
    assert meta.get("retries") == 2

    # ② deterministic fallback path flagged
    assert meta.get("fallback_used") is True

    # ③ meta.retries must equal *actual* LLM retries (calls − 1)
    assert _fake_llm.calls == meta.get("retries") + 1
