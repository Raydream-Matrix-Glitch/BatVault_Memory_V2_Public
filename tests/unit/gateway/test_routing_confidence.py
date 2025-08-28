"""Contract test: `/v2/query` responses must expose routing metadata
according to the Milestone-4/5 specification.

Required keys in ``response["meta"]``:
    • ``function_calls``       – list[str] (non-empty)
    • ``routing_confidence``   – float in [0, 1]
    • ``routing_model_id``     – non-empty str
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.app import app


def test_query_routing_meta_fields() -> None:
    client = TestClient(app)

    resp = client.post(
        "/v2/query",
        json={"text": "Why did Panasonic exit plasma TV production?"},
    )

    assert resp.status_code == 200, "Gateway did not return HTTP 200"

    body = resp.json()
    assert "meta" in body, "meta field missing from response"
    meta = body["meta"]

    required_keys = ("function_calls", "routing_confidence", "routing_model_id")
    missing = [k for k in required_keys if k not in meta]
    assert not missing, f"Missing keys in meta: {', '.join(missing)}"

    # function_calls ─ non-empty list of strings
    assert isinstance(meta["function_calls"], list) and meta["function_calls"], \
        "function_calls must be a non-empty list"
    assert all(isinstance(fn, str) and fn for fn in meta["function_calls"]), \
        "Each function_call must be a non-empty string"

    # routing_confidence ─ numeric 0-1
    confidence = meta["routing_confidence"]
    assert isinstance(confidence, (float, int)), "routing_confidence not numeric"
    assert 0.0 <= confidence <= 1.0, "routing_confidence outside 0-1 range"

    # routing_model_id ─ non-empty string
    model_id = meta["routing_model_id"]
    assert isinstance(model_id, str) and model_id, "routing_model_id must be non-empty string"