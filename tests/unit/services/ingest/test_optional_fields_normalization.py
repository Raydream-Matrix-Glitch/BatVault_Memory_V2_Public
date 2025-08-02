from ingest.pipeline.normalize import normalize_event, normalize_decision


def test_event_tags_and_snippet_normalised():
    raw_evt = {
        "id": "E-123",
        "summary": "Server reboot",
        "description": "Alice rebooted server. Everything returns to normal.",
        "timestamp": "2025-06-01T00:00:00Z",
        "tags": ["Incident", "high-priority"],
    }

    evt = normalize_event(raw_evt)

    # tags → slug-lowercase, deduped & sorted
    assert evt["tags"] == ["high-priority", "incident"]

    # snippet auto-derived from first sentence when not provided
    assert evt["snippet"] == "Alice rebooted server"


def test_decision_based_on_and_tags_carried_through():
    raw_dec = {
        "id": "adopt-argo",
        "option": "Adopt ArgoCD",
        "timestamp": "2025-07-01T00:00:00Z",
        "based_on": ["prior-decision"],
        "tags": ["DevOps", "automation"],
    }

    dec = normalize_decision(raw_dec)

    assert dec["based_on"] == ["prior-decision"]
    assert dec["tags"] == ["automation", "devops"]