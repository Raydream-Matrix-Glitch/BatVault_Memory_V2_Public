"""Additional contract test: verifies that all required routing metadata
is surfaced to callers of `/v2/query` (Milestone-4/5)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.app import app


def _assert_meta(meta: dict) -> None:
    required = ("function_calls", "routing_confidence", "routing_model_id")
    for key in required:
        assert key in meta, f"{key} missing from meta"

    # function_calls – list[str] non-empty
    assert isinstance(meta["function_calls"], list) and meta["function_calls"], \
        "function_calls must be a non-empty list of strings"
    assert all(isinstance(fn, str) and fn for fn in meta["function_calls"]), \
        "Each function_call entry must be a non-empty string"

    # routing_confidence – float 0-1
    conf = meta["routing_confidence"]
    assert isinstance(conf, (float, int)) and 0.0 <= conf <= 1.0, \
        "routing_confidence must be numeric in [0, 1]"

    # routing_model_id – non-empty str
    model_id = meta["routing_model_id"]
    assert isinstance(model_id, str) and model_id, \
        "routing_model_id must be a non-empty string"


def test_query_meta_fields_are_returned() -> None:
    client = TestClient(app)

    resp = client.post(
        "/v2/query",
        json={"text": "Who decided to exit plasma TV production?"},  # variant phrasing
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "meta" in body, "meta field missing"

    _assert_meta(body["meta"])