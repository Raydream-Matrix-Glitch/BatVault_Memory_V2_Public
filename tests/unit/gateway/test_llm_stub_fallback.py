"""Deterministic fallback test when the LLM is disabled.

Milestones 3 & 4 specify that if the environment variable
``OPENAI_DISABLED=1`` is set, the Gateway must return a *stable*,
non-empty ``answer.short_answer`` so automated tests (and developers)
can rely on predictable behaviour.

This test calls `/v2/query` twice under that condition and expects the
same short_answer each time.  It should **fail** until the deterministic
stub is implemented.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.app import app


def _short_answer(resp) -> str:
    return resp.json().get("answer", {}).get("short_answer", "")


def test_deterministic_llm_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate “LLM disabled” environment
    monkeypatch.setenv("OPENAI_DISABLED", "1")

    client = TestClient(app)

    resp1 = client.post("/v2/query", json={"text": "Why did Panasonic exit plasma TV production?"})
    resp2 = client.post("/v2/query", json={"text": "Why did Panasonic exit plasma TV production?"})

    assert resp1.status_code == 200 and resp2.status_code == 200, "endpoint did not return 200"

    s1 = _short_answer(resp1)
    s2 = _short_answer(resp2)

    assert s1 and s2, "short_answer must not be empty"
    assert s1 == s2, "fallback short_answer must be deterministic"