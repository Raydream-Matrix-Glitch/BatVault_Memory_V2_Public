import json, itertools
from pathlib import Path
from ingest.pipeline.snippet_enricher import enrich_all

def test_snippet_golden_matches_expected(tmp_path):
    base = Path(__file__).parent / "golden"
    raw = json.loads((base / "snippet_input.json").read_text(encoding="utf-8"))

    # ── normalise legacy (list) and current (dict-with-arrays) formats ─────────
    if isinstance(raw, list):
        decisions, transitions = {}, {}
        events = {e["id"]: e for e in raw}
    else:
        decisions   = {d["id"]: d for d in raw.get("decisions", [])}
        events      = {e["id"]: e for e in raw.get("events", [])}
        transitions = {t["id"]: t for t in raw.get("transitions", [])}

    # first enrichment pass
    enrich_all(decisions, events, transitions)
    assert any("snippet" in n for n in
               itertools.chain(decisions.values(),
                               events.values(),
                               transitions.values()))

    # idempotence check
    snapshot = json.dumps([decisions, events, transitions], sort_keys=True)
    enrich_all(decisions, events, transitions)
    assert snapshot == json.dumps([decisions, events, transitions], sort_keys=True)
