from gateway.selector import truncate_evidence, bundle_size_bytes
from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
import orjson

def _oversize_ev(count: int=30):
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="A"))
    ev.events = [{"id":f"E{i}", "timestamp":"2025-07-01T00:00:00Z", "summary":"x"} for i in range(count)]
    ev.transitions = WhyDecisionTransitions()
    ev.allowed_ids = ["A"]+[f"E{i}" for i in range(count)]
    return ev

def test_selector_truncates():
    ev0 = _oversize_ev()
    ev1, meta = truncate_evidence(ev0)
    assert meta["selector_truncation"]
    assert len(ev1.events) < len(ev0.events)
    assert set(ev1.allowed_ids) >= {ev1.anchor.id, *[e["id"] for e in ev1.events]}
