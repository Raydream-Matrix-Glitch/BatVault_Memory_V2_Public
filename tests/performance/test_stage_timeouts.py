# Stage-level latency budget smoke-test
import os, requests, pytest

STAGE_BUDGETS = {"search_ms": 800, "expand_ms": 250, "enrich_ms": 600}
RUNS = int(os.getenv("STAGE_TIMEOUT_RUNS", "10"))


@pytest.mark.performance
def test_stage_level_budgets(gw_url):
    url = f"{gw_url}/debug/timings"
    try:
        r = requests.get(url, timeout=3)
    except requests.RequestException:
        pytest.skip("Gateway debug timings endpoint not reachable")
    if r.status_code == 404:
        pytest.skip("Gateway debug timings endpoint not enabled")

    for _ in range(RUNS):
        timings = requests.get(url, timeout=3).json()
        for stage, budget in STAGE_BUDGETS.items():
            assert timings.get(stage, 0) <= budget, f"{stage} {timings[stage]} ms > {budget} ms"