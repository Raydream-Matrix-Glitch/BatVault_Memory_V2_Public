from gateway.selector import truncate_evidence, bundle_size_bytes
import orjson
from core_config.constants import SELECTOR_TRUNCATION_THRESHOLD

def _oversize_ev():
    """Build a bundle guaranteed to exceed the truncation threshold."""
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="A"))
    i = 0
    while True:
        ev.events.append({"id": f"E{i}",
                          "timestamp": "2025-07-01T00:00:00Z",
                          "summary": "x" * 120})
        ev.allowed_ids.append(f"E{i}")
        if bundle_size_bytes(ev) > SELECTOR_TRUNCATION_THRESHOLD + 256:
            break
        i += 1
    return ev

def test_selector_truncates():
    ev0 = _oversize_ev()
    ev1, meta = truncate_evidence(ev0)
    assert meta["selector_truncation"]
    assert len(ev1.events) < len(ev0.events)
    assert set(ev1.allowed_ids) >= {ev1.anchor.id, *[e["id"] for e in ev1.events]}
