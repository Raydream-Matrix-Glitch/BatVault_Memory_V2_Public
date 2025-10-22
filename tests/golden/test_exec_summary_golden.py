import json
import os
import httpx
import pytest
from fastapi.testclient import TestClient

from core_utils.fingerprints import canonical_json

# Canonical fixture paths
HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")
EXP = os.path.join(HERE, "expected")

with open(os.path.join(FIX, "case_basic.memory_view.json"), "r", encoding="utf-8") as f:
    MEMORY_VIEW = json.load(f)

with open(os.path.join(FIX, "case_basic.enrich.json"), "r", encoding="utf-8") as f:
    ENRICH_ITEMS = json.load(f)

with open(os.path.join(EXP, "case_basic.response.json"), "r", encoding="utf-8") as f:
    EXPECTED_RESPONSE = json.load(f)

# ---- httpx patching to stub Memory API ------------------------------------
class _PatchedAsyncClient(httpx.AsyncClient):
    async def request(self, method, url, *args, **kwargs):
        u = httpx.URL(url)
        p = u.path
        if method == "POST" and p == "/api/graph/expand_candidates":
            return httpx.Response(200, json=MEMORY_VIEW)
        if method == "POST" and p == "/api/enrich/batch":
            return httpx.Response(200, json={
                "items": ENRICH_ITEMS,
                "policy_fp": MEMORY_VIEW["meta"]["policy_fp"],
                "snapshot_etag": MEMORY_VIEW["meta"]["snapshot_etag"],
            })
        return await super().request(method, url, *args, **kwargs)

@pytest.fixture(autouse=True)
def _patch_httpx_asyncclient(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)
    yield

def _canonical_response_bytes(obj: dict) -> bytes:
    """Canonicalise only the public response block (ignore signature timestamps)."""
    if "response" in obj:
        obj = obj["response"]
    return canonical_json(obj)

def _assert_orientation_invariants(edges: list[dict]) -> None:
    for e in edges:
        if e["type"] == "ALIAS_OF":
            assert "orientation" not in e, f"ALIAS_OF edge must not be oriented: {e}"
        else:
            assert e.get("orientation") in {"preceding", "succeeding"}, f"Missing orientation on causal edge: {e}"

def test_exec_summary_golden_end_to_end():
    # Deterministic environment
    os.environ.setdefault("BATVAULT_SCHEMAS_DIR", "packages/core_models/src/core_models/schemas")
    os.environ.setdefault("WHY_POLICY_ID", "why_v1")
    os.environ.setdefault("WHY_PROMPT_ID", "why_v1.0")
    os.environ.setdefault("GATEWAY_LOAD_SHED_REFRESH_MS", "0")
    os.environ.setdefault("GATEWAY_MINIO_DISABLED", "1")
    os.environ.setdefault("MEMORY_API_URL", "http://memory.local")

    from gateway.app import app
    client = TestClient(app)
    req = {"question": "why decision", "anchor": MEMORY_VIEW["anchor"]["id"]}
    res = client.post("/v2/query", json=req)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert "response" in payload, "missing 'response' in gateway output"

    # Canonical byte-for-byte comparison (drift kill-switch)
    got = _canonical_response_bytes(payload)
    exp = _canonical_response_bytes(EXPECTED_RESPONSE)
    assert got == exp, f"canonical response.json drifted.\n--- got ---\n{got.decode()}\n--- exp ---\n{exp.decode()}"

    # Fingerprints must match exactly
    meta = payload["response"]["meta"]
    assert meta["fingerprints"]["graph_fp"] == EXPECTED_RESPONSE["response"]["meta"]["fingerprints"]["graph_fp"]
    assert meta["allowed_ids_fp"] == EXPECTED_RESPONSE["response"]["meta"]["allowed_ids_fp"]
    assert meta["bundle_fp"] == EXPECTED_RESPONSE["response"]["meta"]["bundle_fp"]

    # Orientation invariants
    _assert_orientation_invariants(payload["response"]["graph"]["edges"])

    # Cited IDs subset of allowed_ids
    cited = set(payload["response"]["answer"]["cited_ids"])
    allowed = set(payload["response"]["meta"]["allowed_ids"])
    assert cited.issubset(allowed), f"cited_ids not subset of allowed_ids: {cited - allowed}"