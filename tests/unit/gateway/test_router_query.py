# tests/unit/gateway/test_query_route_contract.py

import pathlib
import json
import httpx
import gateway.app as gw_app
import gateway.resolver as gw_resolver
from gateway.app import app
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
#  Fixture loading                                                            #
# --------------------------------------------------------------------------- #
def _fixture_root() -> pathlib.Path:
    """
    Locate the canonical memory/fixtures directory by walking up
    from this test file until found.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

FIXTURES = _fixture_root()
DECISION_ID = "panasonic-exit-plasma-2012"

_decision_json = json.loads(
    (FIXTURES / "decisions" / f"{DECISION_ID}.json").read_text(encoding="utf-8")
)

class DummyResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"x-snapshot-etag": "dummy"}

    def json(self):
        return self._payload


def _dummy_get(url, *args, **kwargs):
    if url.endswith(f"/api/enrich/decision/{DECISION_ID}"):
        return DummyResponse(_decision_json)
    return DummyResponse({})


def _dummy_post(url, *args, **kwargs):
    if url.endswith("/api/graph/expand_candidates"):
        return DummyResponse({
            "matches": [
                {
                    "id": DECISION_ID,
                    "title": _decision_json.get("option"),
                }
            ]
        })
    return DummyResponse({})

# Patch httpx in the gateway app to use the dummies
gw_app.httpx.get = _dummy_get
gw_app.httpx.post = _dummy_post

# AsyncClient stub so any async HTTP call is short-circuited
_REAL_CLIENT = httpx.AsyncClient
def _mock_async_client(*args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={"matches": [{"id": DECISION_ID}]},
            headers={"x-snapshot-etag": "dummy"},
        )
    )
    return _REAL_CLIENT(*args, **kwargs)

gw_app.httpx.AsyncClient = _mock_async_client

# Stub the resolver layer so we never hit Redis/Arango during tests
async def _dummy_resolver(text: str):
    return {"id": DECISION_ID, "score": 1.0}

gw_resolver.resolve_decision_text = _dummy_resolver

# --------------------------------------------------------------------------- #
#  Contract assertions                                                        #
# --------------------------------------------------------------------------- #
def test_query_route_contract():
    """Router must forward NL query → matches list with snippets & ids."""
    client = TestClient(app)
    resp = client.post("/v2/query", json={"text": "Why did Panasonic exit plasma TV production?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "matches" in body and isinstance(body["matches"], list)
    assert any(m["id"] == DECISION_ID for m in body["matches"])
    for m in body["matches"]:
        assert "match_snippet" in m
