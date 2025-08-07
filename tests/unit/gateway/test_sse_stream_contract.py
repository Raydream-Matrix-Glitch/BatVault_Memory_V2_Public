# tests/unit/gateway/test_sse_stream_contract.py
from fastapi.testclient import TestClient
from gateway.app import app
import pytest

# Mark this whole module as an expected xfail until Milestone-4 SSE streaming lands
pytestmark = pytest.mark.xfail(
    reason="Milestone-4: SSE streaming not yet implemented",
    strict=True,
)

def test_v2_query_streaming_contract():
    """
    /v2/query must yield Server-Sent-Events that:
      • start with an 'event: short_answer' line
      • contain JSON data chunks terminated by a single \\n\\n
      • finish with 'event: done'
    """
    client = TestClient(app)
    resp = client.post("/v2/query?stream=true",
                       json={"query": "Why did Panasonic exit plasma?"},
                       stream=True)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    chunks = list(resp.iter_text())
    assert any("event: short_answer" in c for c in chunks)
    assert chunks[-1].strip() == "event: done"


