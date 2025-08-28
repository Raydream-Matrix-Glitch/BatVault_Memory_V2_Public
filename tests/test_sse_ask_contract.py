"""Contract test: `/v2/ask` must stream the validated *short_answer*
as Server-Sent Events when `?stream=true` is requested.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.app import app  # FastAPI instance under test
from tests.helpers.sse_asserts import assert_sse_short_answer


# Milestone-4+: `/v2/ask` is a **structured** endpoint.
# We therefore send a proper `anchor_id` (decision slug) and verify that
# the Gateway streams the validated *short_answer* tokens as SSE.
@pytest.mark.parametrize(
    "payload",
    [
        {
            "intent": "why_decision",
            "anchor_id": "panasonic-exit-plasma-2012",
        },
    ],
)
def test_sse_short_answer_stream_for_ask(payload: dict) -> None:
    client = TestClient(app)

    # Baseline stream (no event framing)
    with client.stream(
        "POST",
        "/v2/ask?stream=true",
        json=payload,
    ) as resp:
        assert_sse_short_answer(resp)

    # also verify event-framed streaming
    # Extended contract – event-framed chunks
    with client.stream(
        "POST",
        "/v2/ask?stream=true&include_event=true",
        json=payload,
    ) as resp_evt:
        assert_sse_short_answer(resp_evt)