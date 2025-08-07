import pytest

from gateway.templater import deterministic_short_answer, validate_and_fix
from core_models.models import WhyDecisionAnswer

ANCHOR_ID = "panasonic-exit-plasma-2012"


@pytest.mark.parametrize(
    "anchor_id, events_n, preceding_n, succeeding_n, supporting_n, allowed_n, expected",
    [
        (
            ANCHOR_ID,
            0,
            0,
            0,
            1,
            1,
            "Decision A1: 0 event(s), 0 preceding, 0 succeeding. Cited 1/1 evidence item(s).",
        ),
        (
            "long-anchor-xyz",
            5,
            2,
            3,
            4,
            10,
            "Decision long-anchor-xyz: 5 event(s), 2 preceding, 3 succeeding. Cited 4/10 evidence item(s).",
        ),
    ],
)
def test_deterministic_short_answer(
    anchor_id,
    events_n,
    preceding_n,
    succeeding_n,
    supporting_n,
    allowed_n,
    expected,
):
    """
    The templater must always produce the same prefix for given inputs,
    truncated to 320 chars if needed.
    """
    result = deterministic_short_answer(
        anchor_id, events_n, preceding_n, succeeding_n, supporting_n, allowed_n
    )
    assert result == expected


def test_validate_and_fix_no_change():
    """
    If supporting_ids are already a subset of allowed and include the anchor,
    validate_and_fix must leave them unchanged.
    """
    original = WhyDecisionAnswer(short_answer="x", supporting_ids=[ANCHOR_ID, "B1"])
    anchor = ANCHOR_ID
    allowed = [ANCHOR_ID, "B1"]
    fixed, changed, errors = validate_and_fix(original, allowed, anchor)

    assert not changed
    assert errors == []
    assert fixed.supporting_ids == [ANCHOR_ID, "B1"]


def test_validate_and_fix_remove_and_add_anchor():
    """
    If supporting_ids contain items not in allowed, and/or the anchor is missing,
    validate_and_fix must correct them and flag that a fallback was used.
    """
    original = WhyDecisionAnswer(short_answer="x", supporting_ids=["X1", "Y1"])
    anchor = ANCHOR_ID
    allowed = [ANCHOR_ID, "Y1"]

    fixed, changed, errors = validate_and_fix(original, allowed, anchor)

    assert changed is True
    assert any(
        "supporting_ids adjusted to fit allowed_ids and include anchor" in e
        for e in errors
    )
    # Anchor first, then any other allowed IDs
    assert fixed.supporting_ids == [ANCHOR_ID, "Y1"]
