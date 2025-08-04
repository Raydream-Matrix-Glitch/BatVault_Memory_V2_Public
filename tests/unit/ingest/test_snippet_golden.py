import json
from pathlib import Path
from ingest.pipeline.snippet_enricher import enrich_all

def test_snippet_golden_matches_expected(tmp_path):
    base = Path(__file__).parent / "golden"
    data = json.loads((base / "snippet_input.json").read_text(encoding="utf-8"))
    decisions = data["decisions"]
    events = data["events"]
    transitions = data["transitions"]
    enrich_all(decisions, events, transitions)
    expected = json.loads((base / "snippet_expected.json").read_text(encoding="utf-8"))
    # Compare only the snippet fields
    assert decisions["d1"]["snippet"] == expected["decisions"]["d1"]["snippet"]
    assert events["e1"]["snippet"] == expected["events"]["e1"]["snippet"]
    assert transitions["t1"]["snippet"] == expected["transitions"]["t1"]["snippet"]
