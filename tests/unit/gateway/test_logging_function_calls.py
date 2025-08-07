# tests/unit/gateway/test_logging_function_calls.py
import json, re
from gateway.app import app
from fastapi.testclient import TestClient
import pytest

# Mark this whole module as an expected xfail until Milestone-4 structured-logging arrives
pytestmark = pytest.mark.xfail(
    reason="Milestone-4: structured function-calls logging not yet implemented",
    strict=True,
)

def test_function_routing_logging(monkeypatch):
    captured = {}
    def _capture_log(level, msg, extra=None, **kw):
        if msg == "intent_completed":
            captured.update(extra or {})
    monkeypatch.setattr("core_logging.logger.Logger.info", _capture_log)

    client = TestClient(app)
    client.post("/v2/query",
                json={"query": "similar to Pana…", "functions":["search_similar"]})

    assert "function_calls"  in captured
    assert "routing_confidence" in captured
    assert captured.get("routing_model_id")  # must not be empty
