from pathlib import Path
import json, httpx, gateway.app as gw_app, gateway.resolver as gw_resolver
from gateway.app import app
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
#  Constants & helpers                                                        #
# --------------------------------------------------------------------------- #
DECISION_ID = "panasonic-exit-plasma-2012"
FIXTURES = Path(__file__).resolve().parents[5] / "memory" / "fixtures"
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


# --------------------------------------------------------------------------- #
#  Stubs: Memory-API → Gateway                                               #
# --------------------------------------------------------------------------- #
def _dummy_get(url, *_, **__):
    if url.endswith(f"/api/enrich/decision/{DECISION_ID}"):
        return DummyResponse(_decision_json)
    return DummyResponse({})


def _dummy_post(url, *_, **__):
    if url.endswith("/api/graph/expand_candidates"):
        # Matches array is what /v2/query forwards
        return DummyResponse(
            {
                "matches": [
                    {
                        "id": DECISION_ID,
                        "title": _decision_json.get("option"),
                        # router adds match_snippet later
                    }
                ]
            }
        )
    return DummyResponse({})


gw_app.httpx.get = _dummy_get            # sync calls
gw_app.httpx.post = _dummy_post


# AsyncClient stub so _any_ async HTTP call is short-circuited
def _mock_async_client(*a, **kw):
    kw["transport"] = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={"matches": [{"id": DECISION_ID}]},
            headers={"x-snapshot-etag": "dummy"},
        )
    )
    return httpx.AsyncClient(*a, **kw)


gw_app.httpx.AsyncClient = _mock_async_client


# Stub the resolver layer so we never hit Redis/Arango during tests
async def _dummy_resolver(text: str):  # noqa: D401
    return {"id": DECISION_ID, "score": 1.0}


gw_resolver.resolve_decision_text = _dummy_resolver

# --------------------------------------------------------------------------- #
#  Contract assertions                                                        #
# --------------------------------------------------------------------------- #
def test_query_route_contract():
    """Router must forward NL query → matches list with snippets & ids."""
    c = TestClient(app)
    resp = c.post("/v2/query", json={"text": "Why did Panasonic exit plasma TV production?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "matches" in body and isinstance(body["matches"], list)
    assert any(m["id"] == DECISION_ID for m in body["matches"])
    # match_snippet is injected by the router when absent
    for m in body["matches"]:
        assert "match_snippet" in m