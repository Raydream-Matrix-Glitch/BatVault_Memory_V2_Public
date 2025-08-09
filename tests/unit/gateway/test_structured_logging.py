"""Contract test for structured logging.

Milestone-4 states that every user-facing Gateway endpoint must emit an
INFO-level log entry that contains the routing decision payload:

    • function_calls
    • routing_confidence
    • routing_model_id

From Milestone-4 onward the implementation is expected to be present.
We monkey-patch ``StructuredLogger.info`` to capture the ``extra`` dicts
that would normally be sent to the log sink."""

from __future__ import annotations

from typing import Any, List

import pytest
from fastapi.testclient import TestClient

from core_logging.logger import StructuredLogger  # production logger
from gateway.app import app


def test_structured_log_contains_routing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Send a representative query and assert that at least one
    ``StructuredLogger.info`` invocation carries the required routing
    keys in its *extra* payload.
    """

    captured: List[Any] = []

    def _capture_info(self, msg: str, *args, **kwargs) -> None:  # type: ignore[no-self-use]
        captured.append(kwargs.get("extra"))

    # Patch the logger for the duration of the test
    monkeypatch.setattr(StructuredLogger, "info", _capture_info, raising=True)

    client = TestClient(app)
    resp = client.post(
        "/v2/query",
        json={"text": "Why did Panasonic exit plasma TV production?"},
    )

    assert captured, "StructuredLogger.info was never called"

    # At least one captured record must contain all mandatory keys
    assert any(
        extra
        and all(
            key in extra for key in ("function_calls", "routing_confidence", "routing_model_id")
        )
        for extra in captured
    ), "routing metadata missing from StructuredLogger.info payload"

    # ------------------------------------------------------------------
    # Milestone-4/5: the same routing metadata must be exposed to the
    # caller in the API response (`meta` block).  Verify that contract.
    # ------------------------------------------------------------------
    assert resp.status_code == 200, "Gateway did not return HTTP 200"

    body = resp.json()
    assert "meta" in body, "meta field missing from response"

    meta = body["meta"]
    required = ("function_calls", "routing_confidence", "routing_model_id")
    missing = [k for k in required if k not in meta]
    assert not missing, f"Missing keys in meta: {', '.join(missing)}"