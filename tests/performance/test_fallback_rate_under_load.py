# File: tests/performance/test_fallback_rate_under_load.py
import concurrent.futures, os, statistics, requests

RUNS        = int(os.getenv("FALLBACK_RATE_RUNS", "60"))   # CI overrideable
WORKERS     = int(os.getenv("FALLBACK_RATE_WORKERS", "6")) # light but concurrent
THRESHOLD   = 0.05                                         # <5 %

def _hit_gateway(gw_url: str, payload: dict) -> bool:
    """Fire a single /v2/ask call and return its fallback_used flag."""
    r = requests.post(f"{gw_url}/v2/ask", json=payload, timeout=15)
    r.raise_for_status()
    return bool(r.json().get("meta", {}).get("fallback_used", False))

def test_fallback_rate_under_load(gw_url):
    """
    Drives a moderate amount of parallel traffic against /v2/ask and
    asserts that the fraction of responses with meta.fallback_used == True
    stays under 5 % (R3.2).
    """
    payload = {
        "intent": "why_decision",
        "decision_ref": "panasonic-exit-plasma-2012",      # fixture slug
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as tp:
        results = list(tp.map(lambda _: _hit_gateway(gw_url, payload), range(RUNS)))

    rate = statistics.mean(results)
    assert rate <= THRESHOLD, f"fallback_used rate {rate:.2%} exceeds the 5 % SLO"
