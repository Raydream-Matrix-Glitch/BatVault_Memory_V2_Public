"""Schema-contract test for Gateway JSON responses.

Milestone-4 specifies that both `/v2/query` and `/v2/ask` must return a
top-level object containing **answer**, **evidence** and **meta**
(note the renamed key).  

* `/v2/query` accepts free-form natural-language **text**  
* `/v2/ask` accepts structured input with an **anchor_id** (decision
  slug) instead of free-form text

`answer.short_answer` must be present and non-empty.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.app import app


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v2/query",
        "/v2/ask",
    ],
)
def test_json_schema_and_short_answer(endpoint: str) -> None:
    client = TestClient(app)

    # `/v2/query` expects free-form text, `/v2/ask` expects a structured anchor_id
    if endpoint == "/v2/query":
        payload = {
            "text": "Why did Panasonic exit plasma TV production?",
        }
    else:  # "/v2/ask"
        payload = {
            "intent": "why_decision",
            "anchor_id": "panasonic-exit-plasma-2012",
        }

    resp = client.post(endpoint, json=payload)

    assert resp.status_code == 200, "endpoint did not return HTTP 200"

    body = resp.json()

    # Required top-level keys
    required = {"answer", "evidence", "meta"}
    assert required.issubset(body.keys()), f"{endpoint} missing keys {required - body.keys()}"

    short_answer = body.get("answer", {}).get("short_answer", "")
    assert short_answer.strip(), f"{endpoint} returned empty short_answer"