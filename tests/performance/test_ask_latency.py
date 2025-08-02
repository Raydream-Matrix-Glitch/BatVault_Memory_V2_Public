import time, statistics, requests

RUNS, BUDGET_MS = 30, 3_000          # p95 ≤ 3 s

def _p95(values):
    values.sort()
    return values[int(len(values) * 0.95) - 1]

def test_ask_p95_latency(gw_url):
    payload = {"intent": "why_decision",
               "decision_ref": "panasonic-exit-plasma-2012"}
    durs = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        r = requests.post(f"{gw_url}/v2/ask", json=payload, timeout=10)
        r.raise_for_status()
        durs.append((time.perf_counter() - t0) * 1_000)
    assert _p95(durs) <= BUDGET_MS, f"p95={statistics.quantiles(durs, n=100)[94]:.1f} ms"
