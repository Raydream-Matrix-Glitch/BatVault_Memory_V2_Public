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
# constants / tiny fixture
# ──────────────────────────
DECISION_ID = "panasonic-exit-plasma-2012"
FIXTURES = Path(__file__).resolve().parents[5] / "memory" / "fixtures"
_DECISION_JSON = json.loads(
    (FIXTURES / "decisions" / f"{DECISION_ID}.json").read_text("utf-8")
)

# ──────────────────────────
# monkey-patches
# ──────────────────────────
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
        ev.__dict__["_retry_count"] = 2   # 2 up-stream attempts → 2 retries
        ev.snapshot_etag = "dummy-etag"
        return ev

    monkeypatch.setattr(_evidence_builder, "build", _dummy_build, raising=True)


@pytest.fixture(autouse=True)
def _force_validator_fallback(monkeypatch):
    """Force the validator to fail once so the templater repair path is taken
    (sets `meta.fallback_used == True`)."""

    monkeypatch.setattr(
        gw_app,
        "validate_response",
        lambda _resp: (False, ["forced schema error"]),
        raising=True,
    )

    # make validate_and_fix report `changed=True`
    import gateway.templater as templater

    monkeypatch.setattr(
        templater,
        "validate_and_fix",
        lambda a, _ids, _anchor: (a, True, ["ids fixed"]),
        raising=True,
    )


# ──────────────────────────
# test
# ──────────────────────────
def test_retry_twice_then_fallback_meta_flags():
    client = TestClient(app)
    r = client.post("/v2/ask", json={"anchor_id": DECISION_ID})

    assert r.status_code == 200, r.text
    meta = r.json().get("meta", {})

    # ① upstream retries surfaced
    assert meta.get("retries") == 2

    # ② deterministic fallback path flagged
    assert meta.get("fallback_used") is True