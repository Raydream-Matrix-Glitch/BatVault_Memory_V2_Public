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
        assert re.search(
            rf"^{name}(?:{{[^}}]*}})?\s+\d+", body, flags=re.MULTILINE
        ), f"Metric {name} not found in /metrics output"