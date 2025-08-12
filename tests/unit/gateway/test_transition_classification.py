import json
from pathlib import Path

import pytest

pytest.importorskip("pytest_asyncio")  # skip if pytest-asyncio not available

from gateway.evidence import EvidenceBuilder


def _fixture_root() -> Path:
    """Walk upward until we find <repo-root>/memory/fixtures."""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")


# Base directory for memory fixtures
FIXTURE_BASE = _fixture_root()


def load_decision(anchor_id: str) -> dict:
    with open(FIXTURE_BASE / "decisions" / f"{anchor_id}.json", "r") as f:
        return json.load(f)


def load_transitions(trans_ids: list[str]) -> list[dict]:
    docs: list[dict] = []
    for tid in trans_ids:
        p = FIXTURE_BASE / "transitions" / f"{tid}.json"
        with open(p, "r") as f:
            docs.append(json.load(f))
    return docs


def load_events(eids: list[str]) -> list[dict]:
    evs: list[dict] = []
    for eid in eids:
        p = FIXTURE_BASE / "events" / f"{eid}.json"
        with open(p, "r") as f:
            evs.append(json.load(f))
    return evs


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


@pytest.mark.asyncio
async def test_transition_classification(monkeypatch):
    """
    EvidenceBuilder must hydrate transitions from the anchor's transitions field
    and classify them into preceding/succeeding based on the anchor's role in
    each link.  The allowed_ids list must include both transition IDs.
    """
    anchor_id = "panasonic-exit-plasma-2012"
    decision = load_decision(anchor_id)
    # Extract transition IDs from the anchor decision fixture
    trans_ids = decision.get("transitions", [])
    trans_docs = load_transitions(trans_ids)
    # Extract supporting event IDs from the anchor decision fixture
    event_ids = decision.get("supported_by", [])
    events = load_events(event_ids)

    # Stub AsyncClient to serve enrich_decision, enrich_transition and expand_candidates
    class MockClient:
        async def get(self, url):
            # /api/enrich/decision/{anchor_id}
            if url.startswith("/api/enrich/decision/"):
                return DummyResp(decision, {"snapshot_etag": f"{anchor_id}-etag"})
            # /api/enrich/transition/{tid}
            if url.startswith("/api/enrich/transition/"):
                tid = url.split("/")[-1]
                for td in trans_docs:
                    if td.get("id") == tid:
                        return DummyResp(td, {"snapshot_etag": f"{tid}-etag"})
                # Simulate 404 for unknown IDs
                resp = DummyResp({}, {})
                resp.status_code = 404
                return resp
            # Any other GET returns empty body
            return DummyResp({}, {})

        async def post(self, url, json):  # noqa: A003
            # /api/graph/expand_candidates
            if url.startswith("/api/graph/expand_candidates"):
                # Provide neighbour events only; transitions come solely from anchor
                return DummyResp({"neighbors": {"events": events, "transitions": []}, "meta": {}}, {})
            return DummyResp({}, {})

        async def aclose(self):
            pass

    # Patch httpx.AsyncClient to our mock
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", MockClient)

    # Patch redis out to avoid actual cache writes
    class DummyRedis:
        def get(self, key):
            return None

        def setex(self, key, ttl, value):
            pass

    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: DummyRedis())

    builder = EvidenceBuilder()
    ev = await builder.build(anchor_id)
    # Ensure exactly one preceding and one succeeding transition
    assert len(ev.transitions.preceding) == 1
    assert len(ev.transitions.succeeding) == 1
    pre_id = ev.transitions.preceding[0]["id"]
    suc_id = ev.transitions.succeeding[0]["id"]
    # Identify orientation from fixture: for anchor to/ from fields
    for td in trans_docs:
        if td.get("to") == anchor_id:
            assert td["id"] == pre_id
        if td.get("from") == anchor_id:
            assert td["id"] == suc_id
    # allowed_ids must include both transition IDs
    assert pre_id in ev.allowed_ids
    assert suc_id in ev.allowed_ids