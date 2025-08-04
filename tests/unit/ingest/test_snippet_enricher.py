from ingest.pipeline.snippet_enricher import enrich_all

def test_enrich_all_sets_snippets_deterministically():
    decisions = {"d1": {"id": "d1", "rationale": "Why we chose X over Y. "*20}}
    events = {"e1": {"id": "e1", "summary": "Launch complete and docs updated."}}
    transitions = {"t1": {"id": "t1", "reason": "Y depends on X"}}
    enrich_all(decisions, events, transitions)
    assert decisions["d1"]["snippet"].endswith("…") or len(decisions["d1"]["snippet"]) <= 160
    assert events["e1"]["snippet"] == "Launch complete and docs updated."
    assert transitions["t1"]["snippet"] == "Y depends on X"
    # re-run to ensure idempotence (no changes)
    prev = (decisions["d1"]["snippet"], events["e1"]["snippet"], transitions["t1"]["snippet"])
    enrich_all(decisions, events, transitions)
    assert prev == (decisions["d1"]["snippet"], events["e1"]["snippet"], transitions["t1"]["snippet"])
