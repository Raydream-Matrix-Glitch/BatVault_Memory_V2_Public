from gateway.match_snippet import build_match_snippet

def test_build_match_snippet_highlights_query_terms_and_is_deterministic():
    match = {"id": "d-abc", "content": "Choosing Redis over Memcached due to latency and tooling support."}
    q = "redis latency"
    s1 = build_match_snippet(match, q)
    s2 = build_match_snippet(match, q)
    assert s1 == s2
    assert "Redis" in s1 or "redis" in s1
    assert "latency" in s1
