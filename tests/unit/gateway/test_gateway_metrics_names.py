"""
+Smoke-test that the *new* Gateway metrics added for Milestone-3
(`total_neighbors_found`, `selector_truncation`, etc.) are registered and
exposed on ``/metrics``.  We *only* assert that the *metric names* appear; we
intentionally avoid making any claims about the current values or label
cardinality so that this test remains stable as implementation details evolve.
"""

import re


def test_gateway_metric_names_present(test_client_gateway):
    resp = test_client_gateway.get("/metrics")
    assert resp.status_code == 200
    body = resp.text

    expected_names = [
        # k-1 evidence-bundle metrics
        "gateway_total_neighbors_found",
        "gateway_selector_truncation_total",
        "gateway_final_evidence_count",
        "gateway_bundle_size_bytes",
        "gateway_dropped_evidence_ids",
    ]

    for name in expected_names:
        # Match *either* a bare gauge or a *_total counter exposition line.
        assert re.search(
            rf"^{name}(?:{{[^}}]*}})?\s+\d+", body, flags=re.MULTILINE
        ), f"Missing or mis-named metric: {name}"