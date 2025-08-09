import os

import orjson

from gateway import llm_client as llm


def test_summarise_json_stub():
    """The helper must return deterministic JSON when stub mode is active."""

    os.environ["OPENAI_DISABLED"] = "1"  # Force stub path

    envelope = {
        "question": "Why did Panasonic exit plasma TV production?",
        "allowed_ids": ["panasonic-exit-plasma-2012"],
    }

    raw = llm.summarise_json(envelope)
    data = orjson.loads(raw)

    assert data["short_answer"].startswith("STUB ANSWER")
    assert data["supporting_ids"] == ["panasonic-exit-plasma-2012"]
