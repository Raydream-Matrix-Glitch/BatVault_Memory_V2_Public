"""Contract test: `/v2/query` must stream the validated *short_answer*
as Server-Sent Events when `?stream=true` is requested.

This test is expected to **fail** until Milestone-4 streaming support is
implemented.  Do **not** mark it xfail – we want CI to break loudly.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.app import app  # FastAPI instance under test
from tests.helpers.sse_asserts import assert_sse_short_answer


@pytest.mark.parametrize(
    "question",
    [
        "Why did Panasonic exit plasma TV production?",  # canonical example
    ],
)
def test_sse_short_answer_stream(question: str) -> None:
    client = TestClient(app)

    # Baseline stream (no event framing)
    with client.stream(
        "POST",
        "/v2/query?stream=true",
        json={"text": question},
    ) as resp:
        assert_sse_short_answer(resp)

    # Extended contract – event-framed chunks
    with client.stream(
        "POST",
        "/v2/query?stream=true&include_event=true",
        json={"text": question},
    ) as resp:
        assert_sse_short_answer(resp)