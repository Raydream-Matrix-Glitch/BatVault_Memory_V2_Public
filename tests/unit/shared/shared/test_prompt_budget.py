from __future__ import annotations
from shared.prompt_budget import plan_budget, gate_budget

def _render(env):
    import orjson
    return [
        {"role": "system", "content": "json-only"},
        {"role": "user", "content": orjson.dumps(env).decode()},
    ]

class DummyEv:
    def __init__(self, n=10):
        self.events=[{"id": f"e{i}", "summary": "x"*64} for i in range(n)]
        self.transitions={"preceding": [], "succeeding": []}
        self.anchor={"id": "a"}
    def model_dump(self):
        return {"anchor": self.anchor, "events": self.events, "transitions": self.transitions}

def _truncate(ev, *, overhead_tokens: int = 0, desired_completion_tokens=None, context_window=None, guard_tokens=None):
    keep = max(1, min(3, len(ev.events)))
    ev2 = DummyEv(0)
    ev2.events = ev.events[:keep]
    return ev2, {"dropped_evidence_ids": [e["id"] for e in ev.events[keep:]], "selector_truncation": len(ev.events)>keep, "total_neighbors_found": len(ev.events), "final_evidence_count": keep, "prompt_tokens": 0, "max_prompt_tokens": 0}

def test_plan_budget_math():
    env = {"intent": "why_decision", "question": "q", "evidence": {"anchor": {"id": "a"}, "events": [1,2,3], "transitions": {"preceding": [], "succeeding": []}}, "allowed_ids": ["a"]}
    stats = plan_budget(_render, env, context_window=1024, guard_tokens=32, desired_completion_tokens=128)
    assert stats["overhead_tokens"] > 0
    assert stats["evidence_budget_tokens"] >= 0

def test_gate_deterministic_shrink_and_logs():
    env = {"intent": "why_decision", "question": "q", "evidence": {}, "allowed_ids": ["a"], "constraints": {"max_tokens": 256}}
    ev = DummyEv(10)
    gp, _ = gate_budget(_render, _truncate, envelope=env, evidence_obj=ev, context_window=256, guard_tokens=16, desired_completion_tokens=64, max_retries=2, shrink_factor=0.8, jitter_pct=0.0, seed=42)
    assert gp["max_tokens"] <= 64
    assert isinstance(gp["messages"], list)
    gp2, _ = gate_budget(_render, _truncate, envelope=env, evidence_obj=ev, context_window=256, guard_tokens=16, desired_completion_tokens=64, max_retries=2, shrink_factor=0.8, jitter_pct=0.0, seed=42)
    assert gp["prompt_tokens"] == gp2["prompt_tokens"]
