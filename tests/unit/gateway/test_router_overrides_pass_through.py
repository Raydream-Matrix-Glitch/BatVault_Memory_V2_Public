from __future__ import annotations
import os
import json
from gateway import inference_router as llm_router

def test_router_passes_messages_override(monkeypatch):
    # Force control path → vLLM adapter
    monkeypatch.setenv("CANARY_PCT", "0")
    captured = {}
    def fake_generate(endpoint, envelope, *, temperature, max_tokens, messages=None):
        captured["endpoint"] = endpoint
        captured["max_tokens"] = max_tokens
        captured["messages"] = messages
        return json.dumps({"short_answer":"ok","supporting_ids":["a"]})
    monkeypatch.setattr("gateway.llm_adapters.vllm.generate", fake_generate)
    env = {"intent":"why_decision","question":"q","evidence":{},"allowed_ids":["a"],"constraints":{"max_tokens":256}}
    override_msgs = [{"role":"system","content":"S"}, {"role":"user","content":"U"}]
    out = llm_router.call_llm_sync(env, request_id="req1", messages_override=override_msgs, max_tokens_override=64)
    assert captured["messages"] == override_msgs
    assert captured["max_tokens"] == 64
    assert isinstance(out, str)
