from core_utils import compute_request_id, idempotency_key

def test_deterministic_request_id():
    a = compute_request_id("/x", {"a":1}, {"b":2})
    b = compute_request_id("/x", {"a":1}, {"b":2})
    assert a == b and len(a) == 16

def test_idempotency_key():
    k = idempotency_key(None, "/x", {"a":1}, {"b":2})
    assert k == compute_request_id("/x", {"a":1}, {"b":2})
