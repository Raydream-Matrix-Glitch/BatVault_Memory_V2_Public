import time, statistics, requests

RUNS, BUDGET_MS = 30, 4_500          # p95 ≤ 4.5 s

def _p95(values):
    values.sort()
    return values[int(len(values) * 0.95) - 1]

def test_query_p95_latency(gw_url):
    payload = {"text": "Why did Panasonic exit plasma TV production?"}
    durs = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        r = requests.post(f"{gw_url}/v2/query", json=payload, timeout=15)
        r.raise_for_status()
        durs.append((time.perf_counter() - t0) * 1_000)
    assert _p95(durs) <= BUDGET_MS, f"p95={statistics.quantiles(durs, n=100)[94]:.1f} ms"
