import pytest, json
from jsonschema import Draft202012Validator as V
from ingest.cli import load_schema


def test_decision_cannot_have_snippet():
    """`snippet` must not appear in Decision schema."""
    bad = {
        "id": "bad-decision-1",
        "option": "Do thing",
        "rationale": "Because.",
        "timestamp": "2024-01-01T00:00:00Z",
        "decision_maker": "Bob",
        "snippet": "should not be here"
    }
    with pytest.raises(Exception):
        V(load_schema("decision")).validate(bad)