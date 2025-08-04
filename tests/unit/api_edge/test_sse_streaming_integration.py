import time

import pytest
from fastapi.testclient import TestClient

from api_edge.app import app   # FastAPI instance under test


def _consume_stream(resp, timeout_s: float = 2.0):
    """Return the decoded chunks we receive within *timeout_s*."""
    deadline = time.time() + timeout_s
    out = []
    for chunk in resp.iter_content():
        if chunk:
            out.append(chunk.decode("utf-8"))
        if time.time() > deadline:
            break
    return out


@pytest.mark.parametrize("endpoint", ["/stream/demo"])
def test_sse_stream_demo(endpoint):
    client = TestClient(app)
    with client.stream("GET", endpoint) as resp:
        # basic contract
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        # Grab raw network chunks first.
        raw_chunks = _consume_stream(resp)
        #
        # ✨ **Why this massage?**
        # Under Starlette’s TestClient the entire stream can be buffered
        # into one aggregated chunk.  Network-chunk count is therefore not
        # a reliable proxy for the number of SSE *frames*.  We re-split the
        # payload on the SSE frame separator instead.
        joined_payload = "".join(raw_chunks)
        chunks = [frame for frame in joined_payload.split("\n\n") if frame.strip()]

    # at least a few ticks should appear
    assert len(chunks) >= 3
    # first frame shape sanity
    assert chunks[0].lstrip().startswith("event: tick")