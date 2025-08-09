# tests/unit/gateway/test_backlink_derivation_contract.py

from pathlib import Path
import json
import httpx
import json, gateway.app as gw_app
from gateway.app import app
from tests.helpers.memory_api_stub import patch_httpx
from contextlib import ExitStack
import httpx 
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


# --------------------------------------------------------------------------- #
#  Unified HTTPX transport stub (v2 contract)                                #
# --------------------------------------------------------------------------- #
def _httpx_handler(req: httpx.Request) -> httpx.Response:
    """
    In-memory stub that reproduces the Memory-API **v2** contract:
      • /api/graph/expand_candidates → flattened `neighbors` list
      • /api/enrich/decision/<id>    → decision envelope with ETag header
    It covers both sync helpers and `httpx.AsyncClient` traffic.
    """
    if req.url.path.startswith("/api/graph/expand_candidates"):
        return httpx.Response(
            200,
            json={
                "node_id":   DECISION,
                "neighbors": [{**event_json, "type": "event"}],
                "meta":      {"snapshot_etag": "dummy"},
            },
        )

    if req.url.path == f"/api/enrich/decision/{DECISION}":
        return httpx.Response(
            200,
            json=decision_json,
            headers={"snapshot_etag": "dummy"},
        )

    # Fallback → mirrors real API behaviour for unknown paths
    return httpx.Response(404, json={})

# AsyncClient factory used by EvidenceBuilder
def _mock_async_client(*args, **kwargs):
    kwargs["transport"] = httpx.MockTransport(_httpx_handler)
    return httpx.AsyncClient(*args, **kwargs)

# ────────────────────────── unified stub ─────────────────────────── #
# Activate the in-memory Memory-API stub _before_ the Gateway spins up
# any AsyncClient pools, and keep it alive for the lifetime of the test
# module.  ExitStack guarantees deterministic teardown when PyTest
# reloads modules (e.g. `--lf`, xdist, etc.).
_httpx_patch = patch_httpx(node_id=DECISION, event_id=EVENT)
ExitStack().enter_context(_httpx_patch)

# Don’t hit MinIO in unit-tests
gw_app._minio_put_batch = lambda *_a, **_kw: None


# --------------------------------------------------------------------------- #
#  Contract assertions                                                        #
# --------------------------------------------------------------------------- #
def test_backlink_derivation_contract():
    """
    /v2/ask must surface reciprocal links derived by ingest:
      decision.supported_by  ↔  event.led_to
    """
    client = TestClient(app)
    resp   = client.post("/v2/ask", json={"node_id": DECISION})
    assert resp.status_code == 200

    payload = resp.json()
    anchor  = payload["evidence"]["anchor"]
    events  = payload["evidence"]["events"]

    # Fail fast if the Gateway mis-parses the Memory-API response shape
    assert "neighbors" in httpx._memory_api_handler(httpx.Request("POST","/api/graph/expand_candidates")).json(), \
        "Stub not exercising v2 neighbour contract – test invalid"

    if not events:
        print("DEBUG-EVIDENCE:", json.dumps(payload["evidence"], indent=2))
    # Fail fast if normalisation ever regresses again.
    assert events, (
        "Gateway EvidenceBuilder produced 0 events – "
        "probable mismatch with Memory-API neighbour contract or Memory-API stub not active – check patch_httpx usage"
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
