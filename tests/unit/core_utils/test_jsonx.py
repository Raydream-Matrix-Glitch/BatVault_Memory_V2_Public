from core_utils import jsonx

def test_jsonx_roundtrip():
    obj = {"a": 1, "b": ["x", 2]}
    s = jsonx.dumps(obj)
    assert isinstance(s, str)
    assert jsonx.loads(s) == obj