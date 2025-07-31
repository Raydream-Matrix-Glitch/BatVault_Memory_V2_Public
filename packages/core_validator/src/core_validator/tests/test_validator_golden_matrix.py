import pytest

from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    WhyDecisionAnswer,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_validator.validator import validate_response


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
        anchor=WhyDecisionAnchor(id="D1"),
        events=events,
        transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
    )
    # allowed_ids must include anchor + all event/transition IDs
    ev.allowed_ids = (
        {"D1"}
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
        ([], [], [], ["D1"], CompletenessFlags(event_count=0), True),
        # 2. supporting_ids not subset of allowed → invalid
        ([{"id": "E1"}], [], [], ["E1"], CompletenessFlags(event_count=1), False),
        # 3. missing transition citation → invalid
        ([], [{"id": "T1"}], [], ["D1"], CompletenessFlags(event_count=0, has_preceding=True), False),
        # 4. event_count mismatch → invalid
        ([{"id": "E2"}], [], [], ["D1", "E2"], CompletenessFlags(event_count=0), False),
        # 5. fully valid case
        (
            [{"id": "E3"}],
            [{"id": "T2"}],
            [{"id": "T3"}],
            ["D1", "E3", "T2", "T3"],
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
    is_valid, errors = validate_response(resp)

    assert is_valid is valid
    if valid:
        assert errors == []
    else:
        assert errors, "Expected validation errors for invalid input"
