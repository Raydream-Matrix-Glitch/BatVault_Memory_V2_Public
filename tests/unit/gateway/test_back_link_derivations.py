from pathlib import Path
import json, httpx, gateway.app as gw_app
from gateway.app import app
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
#  Fixture loading                                                            #
# --------------------------------------------------------------------------- #
ROOT      = Path(__file__).resolve().parents[5]
FIXTURES  = ROOT / "memory" / "fixtures"
DECISION  = "panasonic-exit-plasma-2012"
EVENT     = "pan-e2"

decision_json = json.loads((FIXTURES / "decisions"  / f"{DECISION}.json").read_text())
event_json    = json.loads((FIXTURES / "events"     / f"{EVENT}.json").read_text())

# Inject reciprocal links so the test asserts ingest-level derivations
decision_json["supported_by"] = [EVENT]
event_json["led_to"]          = [DECISION]


class DummyResponse:
    def __init__(self, payload):
        self._payload   = payload
        self.status_code = 200
        self.headers    = {}

    def json(self):  # FastAPI TestClient uses .json()
        return self._payload


def _dummy_get(url, *a, **kw):
    if url.endswith(f"/api/enrich/decision/{DECISION}"):
        return DummyResponse(decision_json)
    return DummyResponse({})


def _dummy_post(url, *a, **kw):
    if url.endswith("/api/graph/expand_candidates"):
        return DummyResponse(
            {
                "neighbors": {
                    "events": [event_json],
                    "transitions": [],
                },
                "meta": {"snapshot_etag": "dummy"},
            }
        )
    return DummyResponse({})


gw_app.httpx.get  = _dummy_get
gw_app.httpx.post = _dummy_post


def _mock_async_client(*a, **kw):
    kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "dummy"}))
    return httpx.AsyncClient(*a, **kw)


gw_app.httpx.AsyncClient = _mock_async_client


# --------------------------------------------------------------------------- #
#  Contract assertions                                                        #
# --------------------------------------------------------------------------- #
def test_backlink_derivation_contract():
    """
    /v2/ask must surface reciprocal links derived by ingest:
      decision.supported_by  ↔  event.led_to
    """
    c  = TestClient(app)
    rs = c.post("/v2/ask", json={"anchor_id": DECISION})
    assert rs.status_code == 200

    payload = rs.json()
    anchor  = payload["evidence"]["anchor"]
    events  = payload["evidence"]["events"]

    # ① supported_by → event present
    assert EVENT in anchor.get("supported_by", [])

    # ② event.led_to → decision present
    assert any(e["id"] == EVENT and DECISION in e.get("led_to", []) for e in events)

    # ③ Evidence bookkeeping must include the event ID
    assert EVENT in payload["evidence"]["allowed_ids"]