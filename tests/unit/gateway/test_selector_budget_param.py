from __future__ import annotations
from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
from gateway.selector import truncate_evidence

def test_selector_uses_desired_completion_and_context_window():
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id="a"),
        events=[{"id": f"e{i}", "summary": "x"*64} for i in range(40)],
        transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
    )
    # Provide explicit budget knobs from the gate
    meta_ev, meta = truncate_evidence(
        ev,
        overhead_tokens=100,
        desired_completion_tokens=100,
        context_window=4096,
        guard_tokens=32,
    )
    assert meta["max_prompt_tokens"] == 4096 - 100 - 32  # 3964
    assert meta["selector_truncation"] in (True, False)