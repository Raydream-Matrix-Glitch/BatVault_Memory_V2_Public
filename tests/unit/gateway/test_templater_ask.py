from fastapi.testclient import TestClient
import gateway.app as gw_app
from gateway.app import app
import httpx, gateway.app as gw


# Stub external HTTP calls to memory_api
class DummyResponse:
    def __init__(self, json_data):
        self._json = json_data
        # satisfy gateway.app’s expectations
        self.headers = {}
        self.status_code = 200
    def json(self):
        return self._json

def dummy_get(url, **kwargs):
    # Dummy /api/enrich response
    return DummyResponse({"id":"pause-paas-rollout-2024-q3","supported_by":[],"transitions":[]})

def dummy_post(url, json=None, **kwargs):
    # Dummy /api/graph/expand_candidates response (Milestone-4)
    nid = (json or {}).get("node_id")                # echo for contract check
    return DummyResponse({"node_id": nid,
                          "neighbors": [],
                          "meta": {"snapshot_etag": ""}})

# Apply stubs to prevent real HTTP calls
gw_app.httpx.get = dummy_get
gw_app.httpx.post = dummy_post

# keep a reference to the real client before patching
_REAL_CLIENT = httpx.AsyncClient

# ── short-circuit gw.httpx.AsyncClient so no real network is hit ──
def _mock_async_client(*args, **kw):
    kw["transport"] = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": "dummy"})
    )
    return _REAL_CLIENT(*args, **kw)

gw.httpx.AsyncClient = _mock_async_client

def test_templater_returns_contract():
    c = TestClient(app)
    r = c.post("/v2/ask", json={"node_id":"pause-paas-rollout-2024-q3"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == "why_decision"
    assert j["evidence"]["anchor"]["id"] == "pause-paas-rollout-2024-q3"
    assert j["answer"]["supporting_ids"][0] == "pause-paas-rollout-2024-q3"
    assert set(j["evidence"]["allowed_ids"]) >= set(j["answer"]["supporting_ids"])
