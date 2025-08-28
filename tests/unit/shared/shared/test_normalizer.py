import pytest

from shared.normalize import (
    normalize_event,
    normalize_decision,
    normalize_transition,
)


def test_normalize_event_basic():
    """Ensure basic event normalisation applies canonical rules."""
    src = {
        "id": "e1",
        "summary": "Event summary",
        "description": "Details",
        "timestamp": "2021-01-02 03:04:05",
        "tags": ["hello-world", "foo_bar"],
        "unknown": "junk",
    }
    out = normalize_event(src)
    # Identity fields preserved
    assert out["id"] == "e1"
    assert out["summary"] == "Event summary"
    assert out["description"] == "Details"
    # Unknown field removed
    assert "unknown" not in out
    # Tags hyphen replaced with underscore, order preserved
    assert out["tags"] == ["hello_world", "foo_bar"]
    # x-extra inserted
    assert "x-extra" in out and isinstance(out["x-extra"], dict)
    # Type populated
    assert out["type"] == "event"
    # Timestamp coerced to canonical ISO-8601 ending with Z
    assert out["timestamp"].endswith("Z")


def test_normalize_decision_and_transition_rules():
    """Check decision and transition normalisation handles tags and fields."""
    dec = {
        "id": "d1",
        "option": "Do it",
        "rationale": "Because",
        "timestamp": "2010-06-01T12:00:00+02:00",
        "tags": ["a-b", "c"],
        "based_on": ["x"],
        "extra_field": True,
    }
    norm_dec = normalize_decision(dec)
    assert norm_dec["type"] == "decision"
    assert norm_dec["tags"] == ["a_b", "c"]
    # Unknown field dropped
    assert "extra_field" not in norm_dec
    # x-extra present
    assert "x-extra" in norm_dec and isinstance(norm_dec["x-extra"], dict)
    # Timestamp converted to UTC Z
    assert norm_dec["timestamp"].endswith("Z")

    tr = {
        "id": "t1",
        "from": "d1",
        "to": "d2",
        "relation": "causes",
        "reason": "why not",
        "timestamp": "2021-04-01T00:00:00Z",
        "tags": ["t-x"],
        "garbage": 123,
    }
    norm_tr = normalize_transition(tr)
    assert norm_tr["type"] == "transition"
    assert norm_tr["tags"] == ["t_x"]
    assert "garbage" not in norm_tr
    assert "x-extra" in norm_tr


def test_normalize_invalid_timestamp():
    """Invalid timestamps must raise ValueError rather than silently passing."""
    with pytest.raises(ValueError):
        normalize_event({"id": "e", "timestamp": "not a timestamp"})