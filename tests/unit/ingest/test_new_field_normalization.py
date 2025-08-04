import json
from pathlib import Path

import pytest

from ingest.pipeline.normalize import normalize_decision, normalize_event


FIXTURES = (
    Path(__file__)
    .resolve()
    .parents[5]  # …/tests/unit/services/ingest/ → repo root
    / "memory"
    / "fixtures"
)


@pytest.mark.parametrize(
    "rel_path,is_decision",
    [
        ("decisions/panasonic-exit-plasma-2012.json", True),
        ("events/pan-e4.json", False),
    ],
)
def test_new_fields_survive_normalisation(rel_path: str, is_decision: bool) -> None:
    src = FIXTURES / rel_path
    data = json.loads(src.read_text(encoding="utf-8"))

    if is_decision:
        normalised = normalize_decision(data.copy())
        # `based_on` must be present and identical (order preserved)
        assert "based_on" in normalised
        assert normalised["based_on"] == data["based_on"]
    else:
        normalised = normalize_event(data.copy())
        # Events pick up `snippet`
        assert "snippet" in normalised and normalised["snippet"]

    # `tags` → lower-cased, de-duplicated, order-stable
    assert "tags" in normalised, "tags were dropped"
    assert all(t == t.lower() for t in normalised["tags"])
    assert len(normalised["tags"]) == len(set(normalised["tags"]))

    # `x-extra` must pass through untouched for forward compatibility
    assert "x-extra" in normalised and isinstance(normalised["x-extra"], dict)