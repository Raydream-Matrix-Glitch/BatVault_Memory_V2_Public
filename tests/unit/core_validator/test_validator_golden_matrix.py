import pytest

from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    WhyDecisionAnswer,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_validator import validate_response

# ---------- real memory-fixture IDs ----------
ANCHOR_ID       = "panasonic-exit-plasma-2012"
EVENT_ID        = "pan-e2"
TRANS_PRE_ID    = "trans-pan-2010-2012"
TRANS_SUC_ID    = "trans-pan-2012-2014"


def make_response(
    events: list[dict],
    trans_pre: list[dict],
    trans_suc: list[dict],
    supporting_ids: list[str],
    flags: CompletenessFlags,
) -> WhyDecisionResponse:
    """
    Construct a WhyDecisionResponse with the given shape.
    """
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id=ANCHOR_ID),
        events=events,
        transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
    )
    # allowed_ids must include anchor + all event/transition IDs
    ev.allowed_ids = (
        {ANCHOR_ID}
        | {e["id"] for e in events if "id" in e}
        | {t["id"] for t in (trans_pre + trans_suc) if "id" in t}
    )

    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=supporting_ids)
    return WhyDecisionResponse(
        intent="why_decision",
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta={"prompt_id": "p", "policy_id": "p"},
    )


@pytest.mark.parametrize(
    "events, trans_pre, trans_suc, supporting_ids, flags, valid",
    [
        # 1. Only anchor → valid
        ([], [], [], [ANCHOR_ID], CompletenessFlags(event_count=0), True),
        # 2. supporting_ids not subset of allowed → invalid
        ([{"id": EVENT_ID}], [], [], [EVENT_ID], CompletenessFlags(event_count=1), False),
        # 3. missing transition citation → invalid
        ([], [{"id": TRANS_PRE_ID}], [], [ANCHOR_ID],
         CompletenessFlags(event_count=0, has_preceding=True), False),
        # 4. event_count mismatch → invalid
        ([{"id": EVENT_ID}], [], [], [ANCHOR_ID, EVENT_ID],
         CompletenessFlags(event_count=0), False),
        # 5. fully valid case
        (
            [{"id": EVENT_ID}],
            [{"id": TRANS_PRE_ID}],
            [{"id": TRANS_SUC_ID}],
            [ANCHOR_ID, EVENT_ID, TRANS_PRE_ID, TRANS_SUC_ID],
            CompletenessFlags(event_count=1, has_preceding=True, has_succeeding=True),
            True,
        ),
    ],
)
def test_validate_response_matrix(
    events, trans_pre, trans_suc, supporting_ids, flags, valid
):
    """
    Golden matrix: verify validate_response on a variety of edge/corner cases.
    """
    resp = make_response(events, trans_pre, trans_suc, supporting_ids, flags)
    ok, errors = validate_response(resp)
    # The validator never raises fatal errors for contract violations: ok is always True.
    assert ok is True
    if valid:
        # For a fully valid bundle no corrections are needed.
        assert errors == []
    else:
        # Otherwise at least one structured error should be present.
        assert errors, "Expected validation errors for invalid input"
