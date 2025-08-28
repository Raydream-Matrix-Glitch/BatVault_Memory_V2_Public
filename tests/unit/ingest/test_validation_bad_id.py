import pytest, json
from jsonschema import Draft202012Validator as V
from ingest.cli import load_schema

def test_invalid_slug_fails():
    bad = {"id": "Bad_ID", "option": "Ship", "timestamp": "2025-01-01T12:00:00Z"}
    with pytest.raises(Exception):
        V(load_schema("decision")).validate(bad)
