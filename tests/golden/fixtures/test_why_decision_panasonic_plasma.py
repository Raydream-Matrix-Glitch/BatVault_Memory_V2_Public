import json
import os
import pytest
import httpx

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "why_decision_panasonic_plasma.json"
)

@pytest.mark.golden
@pytest.mark.xfail(strict=False, reason="Golden suites become mandatory in Milestone-6")
@pytest.mark.asyncio
async def test_why_decision_panasonic_plasma_snapshot():
    """
    Golden snapshot – keeps future code from silently changing a known answer.
    """
    with open(FIXTURE) as fh:
        fx = json.load(fh)

    async with httpx.AsyncClient(base_url="http://localhost:8000") as c:
        res = await c.post("/v2/ask", json=fx["input"])
        res.raise_for_status()
        body = res.json()

    # --- Content smoke-check -------------------------------------------------
    short = body["answer"]["short_answer"].lower()
    for piece in fx["expected_short_answer_contains"]:
        assert piece.lower() in short

    # --- ID coverage check ---------------------------------------------------
    # The API might list evidence under 'supporting_ids' **or** 'transitions'.
    sup_ids = set(body["answer"].get("supporting_ids", [])) | set(
        body["answer"].get("transitions", [])
    )
    for mid in fx["mandatory_ids"]:
        assert mid in sup_ids
