"""
Ensures per-stage time-outs raise HTTP 504 from the Gateway.
"""

import os, asyncio, importlib
from fastapi.testclient import TestClient
import pytest

# shrink budgets to speed the test
os.environ["TIMEOUT_SEARCH_MS"] = "100"
os.environ["TIMEOUT_EXPAND_MS"] = "100"

from services.gateway import app as gateway_app  # noqa: E402

importlib.reload(gateway_app)
client = TestClient(
    gateway_app.app if hasattr(gateway_app, "app") else gateway_app
)


@pytest.mark.parametrize("stage", ["search", "expand"])
def test_stage_timeout(monkeypatch, stage):
    # build a slow coroutine ≥200 ms
    async def _slow(*_a, **_kw):
        await asyncio.sleep(0.2)
        return {"id": "dummy"}

    import services.gateway.evidence as evidence_mod

    if stage == "search":
        monkeypatch.setattr(evidence_mod, "resolve_anchor", _slow)
    else:
        monkeypatch.setattr(evidence_mod, "expand_graph", _slow)

    resp = client.get("/evidence/dummy?intent=query")
    assert resp.status_code == 504
    assert resp.json()["detail"].startswith(f"{stage} stage timeout")
