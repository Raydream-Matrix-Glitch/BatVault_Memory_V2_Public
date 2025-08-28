"""
Golden test to ensure consistency across the ingest, storage, memory_api
and gateway layers.

This test normalises a decision, event and transition fixture using the
ingest pipeline, verifies that the shared normaliser (used by the
memory_api) does not alter the shape, and checks that the gateway's
allowed‑ID computation matches the canonical helper.  It guards
against future drift by asserting that tags remain lists, timestamps are
rendered in canonical ``...Z`` form, ``x-extra`` exists as a dict, and
that re-normalisation yields identical objects (no unknown fields).
"""

import json
from pathlib import Path

import pytest

from ingest.pipeline.normalize import (
    normalize_decision as ingest_normalize_decision,
    normalize_event as ingest_normalize_event,
    normalize_transition as ingest_normalize_transition,
)
from shared.normalize import (
    normalize_decision as shared_normalize_decision,
    normalize_event as shared_normalize_event,
    normalize_transition as shared_normalize_transition,
)
from core_validator import canonical_allowed_ids
from gateway.templater import build_allowed_ids
from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence, WhyDecisionTransitions


def _fixture_root() -> Path:
    """Locate the canonical memory/fixtures directory by walking up from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")


@pytest.mark.golden
def test_service_level_consistency() -> None:
    fixtures = _fixture_root()
    # Select representative fixtures with tags, rationale and transitions
    dec_path = fixtures / "decisions" / "panasonic-exit-plasma-2012.json"
    evt_path = fixtures / "events" / "pan-e2.json"
    tr_path = fixtures / "transitions" / "trans-pan-2010-2012.json"
    dec_raw = json.loads(dec_path.read_text(encoding="utf-8"))
    evt_raw = json.loads(evt_path.read_text(encoding="utf-8"))
    tr_raw = json.loads(tr_path.read_text(encoding="utf-8"))

    # Ingest normalisation
    dec_ing = ingest_normalize_decision(dec_raw.copy())
    evt_ing = ingest_normalize_event(evt_raw.copy())
    tr_ing = ingest_normalize_transition(tr_raw.copy())

    # Assert ingest added type and x-extra and preserved tags as lists
    for node, node_type in [
        (dec_ing, "decision"),
        (evt_ing, "event"),
        (tr_ing, "transition"),
    ]:
        assert node.get("type") == node_type
        assert "x-extra" in node and isinstance(node["x-extra"], dict)
        assert isinstance(node.get("tags"), list)
        # Canonical timestamps end with 'Z'
        if isinstance(node.get("timestamp"), str):
            assert node["timestamp"].endswith("Z")

    # Shared normalisation (memory_api) should be idempotent on ingest outputs
    assert shared_normalize_decision(dec_ing.copy()) == dec_ing
    assert shared_normalize_event(evt_ing.copy()) == evt_ing
    assert shared_normalize_transition(tr_ing.copy()) == tr_ing

    # Compute canonical allowed_ids via the core validator helper
    allowed_ids_core = canonical_allowed_ids(dec_ing["id"], [evt_ing], [tr_ing])

    # Build evidence and compute allowed_ids via the gateway templater
    ev_model = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(**dec_ing),
        events=[evt_ing],
        transitions=WhyDecisionTransitions(preceding=[tr_ing], succeeding=[]),
    )
    allowed_ids_gateway = build_allowed_ids(ev_model)
    assert allowed_ids_core == allowed_ids_gateway

