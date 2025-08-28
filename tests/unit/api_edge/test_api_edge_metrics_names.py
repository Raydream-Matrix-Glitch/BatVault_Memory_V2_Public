"""
Ensure that API-Edge exports the newly-required latency and fallback metrics.
"""

import re


def test_api_edge_metric_names_present(test_client_api_edge):
    resp = test_client_api_edge.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    expected_names = [
        "api_edge_ttfb_seconds",       # histogram family
        "api_edge_fallback_total",     # counter
    ]

    for name in expected_names:
        if not re.search(rf"^{name}(?:{{[^}}]*}})?\s+\d+", body, flags=re.MULTILINE):
            excerpt = "\n".join(body.splitlines()[:40])
            raise AssertionError(
                f"Metric ‘{name}’ not found in /metrics output.\n"
                f"First 40 lines for context:\n{excerpt}"
        )