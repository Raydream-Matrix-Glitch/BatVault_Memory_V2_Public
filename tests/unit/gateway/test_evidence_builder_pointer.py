import pytest

# Import models and builder from the gateway package.  Note that pytest
# collects these tests under the repository root, so the module paths
# must resolve relative to the test execution environment.  We rely on
# pytest's import machinery (configured in conftest.py) which adds the
# appropriate package roots to sys.path.
from gateway.evidence import EvidenceBuilder
from core_models.models import WhyDecisionEvidence


class DummyResp:
    """Simple httpx.Response stand‑in for JSON payloads and headers."""

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
async def test_pointer_missing(monkeypatch) -> None:
    """
    EvidenceBuilder should gracefully handle the case where the alias
    pointer refers to a missing composite key.  When Redis returns a
    pointer (layout 3) and the subsequent fetch fails, the builder
    must treat it as a cache miss, build a fresh evidence bundle, and
    write it back to the cache.  No exceptions should bubble up.
    """

    anchor_id = "panasonic-exit-plasma-2012"

    # Stub out the HTTP client used by EvidenceBuilder to avoid
    # contacting the actual Memory API.  The dummy client returns
    # minimal decision details (including a snapshot_etag) and no
    # neighbours.
    class FakeClient:
        async def get(self, url, headers=None):
            # Enrich decision
            if url.startswith("/api/enrich/decision/"):
               return DummyResp({"id": anchor_id}, {"snapshot_etag": "etag1"})
            # Enrich event
            if url.startswith("/api/enrich/event/"):
                eid = url.rsplit("/", 1)[-1]
                return DummyResp({"id": eid}, {})
            raise RuntimeError(f"unexpected GET {url}")

        async def post(self, url, json):
            # Expand candidates returns an empty neighbour list
            return DummyResp({"neighbors": [], "meta": {}}, {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self) -> None:
            pass

    # Patch the httpx.AsyncClient in gateway.evidence to our FakeClient
    monkeypatch.setattr("gateway.evidence.httpx.AsyncClient", FakeClient)

    # Record Redis operations for assertions
    calls = {"get": 0, "setex": 0}

    class FakeRedis:
        """Redis stub that returns a pointer followed by a miss."""

        def get(self, key):
            calls["get"] += 1
            # First call: return a pointer value (layout 3)
            if calls["get"] == 1:
                # Value stored under alias_key points to a missing composite key
                return b"missing_composite_key"
            # Second call: pointer lookup misses
            return None

        def setex(self, key, ttl, value) -> None:
            # Capture cache writes
            calls["setex"] += 1

    # Patch Redis.from_url to return our FakeRedis instance
    monkeypatch.setattr("gateway.evidence.redis.Redis.from_url", lambda url: FakeRedis())

    # Build evidence; should not raise
    builder = EvidenceBuilder()
    ev = await builder.build(anchor_id)

    assert isinstance(ev, WhyDecisionEvidence)
    # A fresh bundle should be written once to the cache (one setex call)
    assert calls["setex"] == 1