"""
Smoke-test that the **gateway** exposes the new `artifact_bytes_total` metric.
"""

import re, core_metrics
from fastapi.testclient import TestClient
from services.gateway.src.gateway.app import app as gw_app

# Pre-register so the metric shows up even without actually persisting blobs.
core_metrics.counter("artifact_bytes_total", 0)


def test_gateway_artifact_metric_present() -> None:
    client = TestClient(gw_app)
    res = client.get("/metrics")
    assert res.status_code == 200
    assert re.search(
        r"^artifact_bytes_total(?:\{[^}]*\})?\s+\d+",
        res.text,
        flags=re.MULTILINE,
    ), "artifact_bytes_total not found in /metrics exposition"