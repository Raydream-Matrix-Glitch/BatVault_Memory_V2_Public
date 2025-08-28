import json
from pathlib import Path
import pytest
from contextlib import asynccontextmanager

pytest.importorskip("pytest_asyncio")

from gateway.evidence import EvidenceBuilder

def _fixture_root() -> Path:
    # Walk upward until we find <repo-root>/memory/fixtures.
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise RuntimeError("Fixture root not found")

FIXTURE_BASE = _fixture_root()

def load_decision(anchor_id: str) -> dict:
    with open(FIXTURE_BASE / "decisions" / f"{anchor_id}.json", "r") as f:
        return json.load(f)

def load_transition(tid: str) -> dict:
    with open(FIXTURE_BASE / "transitions" / f"{tid}.json", "r") as f:
        return json.load(f)

class StubResp:
    def __init__(self, json_data=None, status_code=200, headers=None, raise_exc=None):
        self._json = json_data or {}
        self.status_code = status_code
        self.headers = headers or {}
        self._raise_exc = raise_exc

    def json(self):
        return self._json

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class FailingExpandClient:
    """
    Safe-client compatible stub:
    - GET /api/enrich/decision/{id} → returns enriched anchor JSON (with transitions)
    - GET /api/enrich/transition/{tid} → returns full transition docs
    - POST /api/graph/expand_candidates → simulate failure (raises)
    """
    def __init__(self, anchor_json: dict):
        self._anchor_json = dict(anchor_json)
        # Ensure id present
        self._anchor_json.setdefault("id", anchor_json.get("id"))
        # We'll expose calls for debugging if needed
        self.calls = []

    async def get(self, url, *a, **kw):
        self.calls.append(("GET", url))
        if url.startswith("/api/enrich/decision/"):
            # return with a hyphenated snapshot header to avoid "unknown"
            return StubResp(self._anchor_json, 200, headers={"Snapshot-ETag": "etag-ok"})
        if url.startswith("/api/enrich/transition/"):
            tid = url.rsplit("/", 1)[-1]
            return StubResp(load_transition(tid), 200)
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url, *a, **kw):
        self.calls.append(("POST", url))
        if url == "/api/graph/expand_candidates":
            # Simulate expand failure as ConnectTimeout surfaced as exception
            raise RuntimeError("connect timeout")
        raise AssertionError(f"Unexpected POST {url}")

    async def aclose(self):
        pass

@asynccontextmanager
async def _fake_safe_async_client(*a, **kw):
    # 'a' and 'kw' include (timeout=?, base_url=?, _fresh=?)
    # Build client from the actual anchor id in tests by peeking into our closure.
    anchor_id = _fake_safe_async_client._anchor_id
    anchor_json = load_decision(anchor_id)
    # Sanity: ensure there are transitions so classification has something to do
    assert anchor_json.get("transitions"), "Fixture decision lacks transitions"
    client = FailingExpandClient(anchor_json)
    try:
        yield client
    finally:
        await client.aclose()
# poor-man's storage for per-test anchor id
_fake_safe_async_client._anchor_id = ""

@pytest.mark.asyncio
async def test_expand_failure_does_not_block_transition_classification(monkeypatch, caplog):
    """
    If expand_candidates fails, the builder must still hydrate and classify
    transitions present on the anchor. allowed_ids must include those transition ids.
    """
    anchor_id = "panasonic-exit-plasma-2012"
    _fake_safe_async_client._anchor_id = anchor_id

    # Ensure gateway uses our safe client and never constructs raw httpx.AsyncClient
    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("raw httpx.AsyncClient constructed")
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", Boom)
    monkeypatch.setattr("gateway.evidence._safe_async_client", _fake_safe_async_client, raising=True)

    # Stub Redis to a no-op
    class DummyRedis:
        def get(self, k): return None
        def setex(self, k, ttl, v): pass
    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: DummyRedis())

    builder = EvidenceBuilder()
    with caplog.at_level("INFO"):
        ev = await builder.build(anchor_id)

    # Classification produced both sides
    assert len(ev.transitions.preceding) >= 1
    assert len(ev.transitions.succeeding) >= 1

    pre_ids = {t["id"] for t in ev.transitions.preceding}
    suc_ids = {t["id"] for t in ev.transitions.succeeding}

    # Every classified id should also be in allowed_ids
    for tid in pre_ids | suc_ids:
        assert tid in ev.allowed_ids

    # Strategic logs present
    assert any("transitions_hydration_start" in rec.message for rec in caplog.records)
    assert any("transitions_classified" in rec.message for rec in caplog.records)

    # Snapshot etag should have been propagated (not "unknown")
    assert ev.snapshot_etag == "etag-ok"
