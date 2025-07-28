from ingest.pipeline.normalize import normalize_decision, normalize_event, normalize_transition, derive_backlinks

def test_orphan_event_and_decision_empty_arrays_ok():
    d = {"id":"pause-paas-rollout-2024-q3","option":"Pause","timestamp":"2024-07-20T14:30:00Z","supported_by":[],"transitions":[]}
    e = {"id":"B-E1","description":"Q2 overspend 40%","timestamp":"2024-07-19T08:00:00Z","led_to":[]}
    t = {"id":"trans-123","from":"enter-cloud-market-2024-q1","to":"pause-paas-rollout-2024-q3","relation":"causal","timestamp":"2024-08-12T09:05:00Z"}

    nd = normalize_decision(d); ne = normalize_event(e); nt = normalize_transition(t)
    decisions = {nd["id"]: nd}; events = {ne["id"]: ne}; transitions = {nt["id"]: nt}
    derive_backlinks(decisions, events, transitions)

    assert decisions[nd["id"]]["supported_by"] == []  # empty array allowed
    assert events[ne["id"]]["led_to"] == []          # empty array allowed
    # transition should be attached to decision 'to'
    assert "trans-123" in decisions["pause-paas-rollout-2024-q3"]["transitions"]
