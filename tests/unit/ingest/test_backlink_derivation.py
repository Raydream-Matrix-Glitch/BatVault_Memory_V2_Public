from ingest.pipeline.normalize import normalize_decision, normalize_event, derive_backlinks

def test_backlinks_roundtrip():
    d = normalize_decision(
        {"id": "adopt-k3s-2025q1", "option": "Adopt K3s", "timestamp": "2025-01-05T09:00:00Z"}
    )
    e = normalize_event(
        {"id": "E-42", "summary": "Prod outage", "timestamp": "2025-01-04T07:00:00Z", "led_to": ["adopt-k3s-2025q1"]}
    )

    decisions = {d["id"]: d}
    events    = {e["id"]: e}
    derive_backlinks(decisions, events, {})

    assert "e-42" in decisions["adopt-k3s-2025q1"]["supported_by"]
    assert "adopt-k3s-2025q1" in events["e-42"]["led_to"]
