from ingest.pipeline.normalize import (
    normalize_decision,
    normalize_event,
)

# ------------------------------------------------------------------
# Decision ↔ Event link defaults
# ------------------------------------------------------------------
def test_decision_link_arrays_default_to_empty():
    doc = {
        "id": "adopt-loki",
        "option": "Adopt Loki",
        "timestamp": "2025-03-01T00:00:00Z",
    }
    out = normalize_decision(doc)
    for field in ("supported_by", "based_on", "transitions"):
        assert field in out and out[field] == []

def test_event_led_to_defaults_to_empty():
    evt = {
        "id": "E-007",
        "summary": "Demo outage",
        "timestamp": "2025-03-02T00:00:00Z",
    }
    out = normalize_event(evt)
    assert out["led_to"] == []

def test_preserves_existing_arrays():
    doc = {
        "id": "adopt-argo",
        "option": "Adopt ArgoCD",
        "timestamp": "2025-03-03T00:00:00Z",
        "supported_by": ["E-900"],
    }
    out = normalize_decision(doc)
    assert out["supported_by"] == ["E-900"]
