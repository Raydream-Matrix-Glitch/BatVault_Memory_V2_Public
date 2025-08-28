import pathlib
import json
import pytest
from jsonschema import Draft202012Validator as V
from ingest.cli import load_schema

def _fixture_root() -> pathlib.Path:
    """
    Find the canonical memory/fixtures directory by walking up
    from this test file until it’s found.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures directory not found")

ROOT = _fixture_root()

def _infer_schema(doc: dict) -> str:
    """
    Decide which JSON schema to use based on the keys in the document:
      - If it has both "from" & "to": transition
      - If it has "option": decision
      - Otherwise: event
    """
    if {"from", "to"} <= doc.keys():
        return "transition"
    if "option" in doc:
        return "decision"
    return "event"

@pytest.mark.skipif(not ROOT.exists(), reason="memory/fixtures missing")
@pytest.mark.parametrize("p", ROOT.rglob("*.json"))
def test_fixture_passes_schema(p: pathlib.Path):
    data = json.loads(p.read_text())
    schema = load_schema(_infer_schema(data))
    V(schema).validate(data)
