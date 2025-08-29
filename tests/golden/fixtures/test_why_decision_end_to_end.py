import os
import pytest

from fastapi.testclient import TestClient
from gateway.app import app


def _assert_event_shape(evt: dict) -> None:
    """Assert that the event dict contains only whitelisted keys and normalised tags."""
    # Milestone‑5: allow extra normalisation fields on events
    allowed = {
        "id",
        "summary",
        "timestamp",
        "tags",
        "snippet",
        "x-extra",
        "led_to",
        "normalized_amount",
        "currency",
    }
    assert set(evt.keys()).issubset(allowed)
    # ensure tags are lower‑case underscores
    for t in evt.get("tags", []):
        assert t == t.lower()
        assert "-" not in t  # hyphens should be converted to underscores
    # x-extra must exist and be a dict
    assert "x-extra" in evt and isinstance(evt["x-extra"], dict)


@pytest.mark.parametrize("cite_all", [True, False])
def test_why_decision_panasonic_plasma_end_to_end(monkeypatch, cite_all):
    """
    End-to-end test for the Panasonic plasma exit case.  Asserts that:

    * evidence.events contains only atomic events (type == "event") and
      that each event is projected onto the minimal field set.
    * allowed_ids exactly matches the union of anchor, event and transition ids.
    * answer.supporting_ids obey the rules: when CITE_ALL_IDS is true,
      supporting_ids == allowed_ids; otherwise, anchor and all transitions
      must be cited at minimum with the anchor first.
    * Tags are normalised and x-extra is present on all envelopes.
    * The validator emitted no errors for this healthy case.
    """
    # Toggle the CITE_ALL_IDS environment variable
    if cite_all:
        monkeypatch.setenv("CITE_ALL_IDS", "true")
    else:
        monkeypatch.delenv("CITE_ALL_IDS", raising=False)

    client = TestClient(app)
    resp = client.post(
        "/v2/ask",
        json={"intent": "why_decision", "anchor_id": "panasonic-exit-plasma-2012"},
    )
    assert resp.status_code == 200
    body = resp.json()

    evidence = body.get("evidence") or {}
    anchor_id = evidence.get("anchor", {}).get("id")
    assert anchor_id == "panasonic-exit-plasma-2012"
    events = evidence.get("events") or []
    transitions = evidence.get("transitions") or {}
    pre = transitions.get("preceding") or []
    suc = transitions.get("succeeding") or []

    # 1. Events list should contain only atomic events and be projected
    for evt in events:
        # Assert no "type" fields remain on events
        assert (evt.get("type") is None), f"unexpected type on event {evt}"
        _assert_event_shape(evt)

    # 2. allowed_ids must equal the union of anchor, events and transitions
    allowed_ids = evidence.get("allowed_ids") or []
    union_ids = []
    if anchor_id:
        union_ids.append(anchor_id)
    # events by id
    for e in events:
        eid = e.get("id")
        if eid and eid not in union_ids:
            union_ids.append(eid)
    # transitions by id
    for tr in pre + suc:
        tid = tr.get("id")
        if tid and tid not in union_ids:
            union_ids.append(tid)
    assert allowed_ids == union_ids

    # 3. supporting_ids rules
    supp_ids = body.get("answer", {}).get("supporting_ids") or []
    if cite_all:
        # When CITE_ALL_IDS=true, supporting_ids must match allowed_ids exactly
        assert supp_ids == allowed_ids
    else:
        # Otherwise supporting_ids must start with anchor and include all transitions
        assert supp_ids, "supporting_ids should not be empty"
        assert supp_ids[0] == anchor_id
        trans_ids = [tr.get("id") for tr in pre + suc if tr.get("id")]
        for tid in trans_ids:
            assert tid in supp_ids

    # 4. Every envelope (anchor, events, transitions) should have x-extra and normalised tags
    anchor = evidence.get("anchor") or {}
    # Anchor tags normalisation
    for t in (anchor.get("tags") or []):
        assert t == t.lower()
        assert "-" not in t
    assert "x-extra" in anchor and isinstance(anchor.get("x-extra"), dict)
    for tr in pre + suc:
        # transitions should retain only canonical keys; x-extra present
        assert "x-extra" in tr and isinstance(tr.get("x-extra"), dict)
        for t in (tr.get("tags") or []):
            assert t == t.lower()
            assert "-" not in t

    # 5. Validator errors should be empty for this healthy case.  The meta now
    # exposes only the count of validation errors.
    meta = body.get("meta") or {}
    assert meta.get("validator_error_count") == 0


@pytest.mark.parametrize("cite_all", [True, False])
def test_why_decision_minimal_end_to_end(monkeypatch, cite_all):
    """
    End-to-end test for a minimal synthetic decision (demo-min-decision).  Asserts
    the same invariants as the Panasonic test on a small bundle with no
    transitions and possibly no events.
    """
    if cite_all:
        monkeypatch.setenv("CITE_ALL_IDS", "true")
    else:
        monkeypatch.delenv("CITE_ALL_IDS", raising=False)
    client = TestClient(app)
    resp = client.post(
        "/v2/ask",
        json={"intent": "why_decision", "anchor_id": "demo-min-decision"},
    )
    assert resp.status_code == 200
    body = resp.json()
    evidence = body.get("evidence") or {}
    anchor_id = evidence.get("anchor", {}).get("id")
    assert anchor_id == "demo-min-decision"
    events = evidence.get("events") or []
    transitions = evidence.get("transitions") or {}
    pre = transitions.get("preceding") or []
    suc = transitions.get("succeeding") or []
    # No transitions expected for minimal case
    assert not pre and not suc
    # allowed_ids should contain only the anchor and any events
    allowed_ids = evidence.get("allowed_ids") or []
    union_ids = [anchor_id]
    for e in events:
        eid = e.get("id")
        if eid and eid not in union_ids:
            union_ids.append(eid)
    assert allowed_ids == union_ids
    supp_ids = body.get("answer", {}).get("supporting_ids") or []
    if cite_all:
        assert supp_ids == allowed_ids
    else:
        # At minimum the anchor must be cited
        assert supp_ids and supp_ids[0] == anchor_id
    # Check event shapes
    for evt in events:
        assert (evt.get("type") is None)
        _assert_event_shape(evt)
    # x-extra and tags on anchor
    anchor = evidence.get("anchor") or {}
    assert "x-extra" in anchor and isinstance(anchor.get("x-extra"), dict)
    for t in (anchor.get("tags") or []):
        assert t == t.lower()
        assert "-" not in t
    # Validator errors should be empty
    meta = body.get("meta", {})
    assert meta.get("validator_error_count") == 0

