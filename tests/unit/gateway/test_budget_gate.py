from __future__ import annotations
from gateway.budget_gate import run_gate
from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions

def test_gate_outputs_and_fingerprint():
    envelope = {
        "intent": "why_decision",
        "question": "Why?",
        "constraints": {"max_tokens": 256},
        "evidence": {},
        "allowed_ids": ["a"],
    }
    ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="a"), events=[], transitions=WhyDecisionTransitions(preceding=[], succeeding=[]))
    gp = run_gate(envelope, ev, request_id="req_test", model_name="TestModel")
    assert gp.max_tokens > 0
    assert gp.prompt_tokens > 0
    assert gp.fingerprints and "prompt" in gp.fingerprints
