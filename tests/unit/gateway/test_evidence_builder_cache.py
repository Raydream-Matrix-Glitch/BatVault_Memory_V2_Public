from pathlib import Path
import asyncio
import json

import pytest
pytest.importorskip("pytest_asyncio")  # clean “SKIPPED – requires pytest-asyncio”

from gateway.evidence import EvidenceBuilder
from core_models.models import WhyDecisionEvidence
from gateway.evidence import _extract_snapshot_etag


# ---------- fixture helpers -------------------------------------------------
def _fixture_root() -> Path:
    """Walk upward until we find <repo-root>/memory/fixtures."""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

# Base directory for memory fixtures
FIXTURE_BASE = _fixture_root()


class DummyResp:
    """Mocks httpx.Response with json payload and headers."""
    def __init__(self, json_data: dict, headers: dict):
        self._json = json_data
        self.headers = headers
        self.status_code = 200

    def json(self) -> dict:
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

def test__extract_snapshot_etag_variants():
    # underscore form
    r1 = DummyResp({}, {"snapshot_etag": "u1"})
    assert _extract_snapshot_etag(r1) == "u1"
    # hyphenated + case-variant
    r2 = DummyResp({}, {"Snapshot-ETag": "h1"})
    assert _extract_snapshot_etag(r2) == "h1"
    # x-prefixed variant for header-based propagation (Memory‑API)
    r3 = DummyResp({}, {"x-snapshot-etag": "x3"})
    assert _extract_snapshot_etag(r3) == "x3"


def load_decision(anchor_id: str) -> dict:
    path = FIXTURE_BASE / "decisions" / f"{anchor_id}.json"
    with open(path, "r") as f:
        return json.load(f)


def load_neighbors(anchor_id: str) -> tuple[list[dict], list[dict]]:
    # Load all supported_by events
    decision = load_decision(anchor_id)
    events = []
    for eid in decision.get("supported_by", []):
        p = FIXTURE_BASE / "events" / f"{eid}.json"
        with open(p, "r") as f:
            events.append(json.load(f))

    # Split transitions
    trans_pre, trans_suc = [], []
    for tid in decision.get("transitions", []):
        p = FIXTURE_BASE / "transitions" / f"{tid}.json"
        with open(p, "r") as f:
            tr = json.load(f)
        if tr.get("to") == anchor_id:
            trans_pre.append(tr)
        if tr.get("from") == anchor_id:
            trans_suc.append(tr)

    return events, trans_pre, trans_suc


@pytest.mark.asyncio
async def test_cache_hit(monkeypatch):
    """
    First build writes to Redis (setex); second build hits cache and does not call setex again.
    """
    anchor_id = "panasonic-exit-plasma-2012"
    decision = load_decision(anchor_id)
    events, trans_pre, trans_suc = load_neighbors(anchor_id)
    snapshot_etag = f"{decision['id']}-etag"

    class MockClient:
        async def get(self, url):
            return DummyResp(decision, {"snapshot_etag": snapshot_etag})
        async def post(self, url, json):
            # Milestone-3: everything comes back in one flat list
            return DummyResp(
                {"neighbors": events + trans_pre + trans_suc, "meta": {}},
                {},
            )
        async def aclose(self):
            pass

    # Replace HTTP client
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", MockClient)

    calls = {"setex": 0, "get": 0}

    class MockRedis:
        def get(self, key):
            calls["get"] += 1
            # Return cached JSON only on second call
            if calls["get"] > 1:
                cached = {
                    "anchor": {"id": decision["id"]},
                    "events": [],
                    "transitions": {"preceding": [], "succeeding": []},
                    "allowed_ids": [],
                    "supporting_ids": [],
                }
                return json.dumps(cached).encode()
            return None

        def setex(self, key, ttl, value):
            calls["setex"] += 1

    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: MockRedis())

    builder = EvidenceBuilder()
    ev1 = await builder.build(anchor_id)
    assert isinstance(ev1, WhyDecisionEvidence)
    assert calls["setex"] == 1

    ev2 = await builder.build(anchor_id)
    assert isinstance(ev2, WhyDecisionEvidence)
    # No new setex on cache hit
    assert calls["setex"] == 1


@pytest.mark.asyncio
async def test_etag_change_eviction(monkeypatch):
    """
    When the snapshot_etag changes between calls, cache is bypassed and setex is called again.
    """
    anchor_id = "panasonic-exit-plasma-2012"
    decision = load_decision(anchor_id)
    events, trans_pre, trans_suc = load_neighbors(anchor_id)
    etags = ["etag1", "etag2"]

    class MockClient2:
        def __init__(self):
            self._idx = 0

        async def get(self, url):
            etag = etags[self._idx]
            self._idx += 1
            return DummyResp(decision, {"snapshot_etag": etag})

        async def post(self, url, json):
            return DummyResp(
                {"neighbors": events + trans_pre + trans_suc, "meta": {}},
                {},
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", MockClient2)

    calls = {"setex": 0}

    class MockRedis2:
        def get(self, key):
            return None  # Always miss first, forcing setex

        def setex(self, key, ttl, value):
            calls["setex"] += 1

    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: MockRedis2())

    builder = EvidenceBuilder()
    ev1 = await builder.build(anchor_id)
    # internal field present …
    assert ev1.snapshot_etag == etags[0]
    # … but never serialised
    assert "snapshot_etag" not in ev1.model_dump()
    assert calls["setex"] == 1

    ev2 = await builder.build(anchor_id)
    assert ev2.snapshot_etag == etags[1]
    assert "snapshot_etag" not in ev2.model_dump()
    # New etag → new cache write
    assert calls["setex"] == 2
