# tests/unit/gateway/test_backlink_derivation_contract.py

from pathlib import Path
import json
import httpx
import gateway.app as gw_app
from gateway.app import app
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
#  Fixture loading                                                            #
# --------------------------------------------------------------------------- #
def _fixture_root() -> Path:
    """
    Locate the canonical memory/fixtures directory by walking up
    from this test file until found.
    """
    for parent in Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

FIXTURES = _fixture_root()
DECISION = "panasonic-exit-plasma-2012"
EVENT    = "pan-e2"

decision_json = json.loads((FIXTURES / "decisions" / f"{DECISION}.json").read_text())
event_json    = json.loads((FIXTURES / "events"    / f"{EVENT}.json").read_text())

# Inject reciprocal links so the test asserts ingest‐level derivations
decision_json["supported_by"] = [EVENT]
event_json["led_to"]          = [DECISION]


class DummyResponse:
    def __init__(self, payload):
        self._payload    = payload
        self.status_code = 200
        self.headers     = {}

    def json(self):
        # FastAPI TestClient expects a .json() method
        return self._payload


def _dummy_get(url, *args, **kwargs):
    if url.endswith(f"/api/enrich/decision/{DECISION}"):
        return DummyResponse(decision_json)
    return DummyResponse({})


def _dummy_post(url, *args, **kwargs):
    if url.endswith("/api/graph/expand_candidates"):
        return DummyResponse({
            "neighbors": {
                "events": [event_json],
                "transitions": [],
            },
            "meta": {"snapshot_etag": "dummy"},
        })
    return DummyResponse({})


# Patch the httpx calls in the gateway app
gw_app.httpx.get  = _dummy_get
gw_app.httpx.post = _dummy_post


def _mock_async_client(*args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "dummy"}))
    return httpx.AsyncClient(*args, **kwargs)

gw_app.httpx.AsyncClient = _mock_async_client


# --------------------------------------------------------------------------- #
#  Contract assertions                                                        #
# --------------------------------------------------------------------------- #
def test_backlink_derivation_contract():
    """
    /v2/ask must surface reciprocal links derived by ingest:
      decision.supported_by  ↔  event.led_to
    """
    client = TestClient(app)
    resp   = client.post("/v2/ask", json={"anchor_id": DECISION})
    assert resp.status_code == 200

    payload = resp.json()
    anchor  = payload["evidence"]["anchor"]
    events = payload["evidence"]["events"]
    # Fail fast if normalisation ever regresses again.
    assert events, (
        "Gateway EvidenceBuilder produced 0 events – "
        "probable mismatch with Memory-API neighbour contract."
    )
    # ① supported_by → event present
    assert EVENT in anchor.get("supported_by", []), (
        f"Expected reciprocal link {EVENT!r} in decision.supported_by "
        f"but got {anchor.get('supported_by')}"
    )

    # ② event.led_to → decision present
    assert any(e["id"] == EVENT and DECISION in e.get("led_to", []) for e in events)

    # ③ Evidence bookkeeping must include the event ID
    assert EVENT in payload["evidence"]["allowed_ids"]
