from core_utils import prompt_fingerprint, canonical_json

def test_prompt_fingerprint_stable():
    env1 = {"b":2,"a":1}
    env2 = {"a":1,"b":2}
    assert prompt_fingerprint(env1) == prompt_fingerprint(env2)
    assert canonical_json(env1) != canonical_json({"b":2,"a":1,"c":3})
