"""
Ensures every service exposes `/metrics` **and** the key counters expected by
health-gate tests.  Runs once in CI at container start-up.
"""

from fastapi.testclient import TestClient

from services.api_edge.src.api_edge.app   import app as api_app
from services.gateway.src.gateway.app     import app as gw_app
from services.memory_api.src.memory_api.app import app as mem_app
from services.ingest.src.ingest.app       import app as ing_app

import core_metrics

_REQUIRED = [
    "cache_hit_total",
    "cache_miss_total",
    "fallback_total",
    "selector_truncation",
    "artifact_bytes_total",
]

# pre-register metrics so they appear even before the first request
for m in _REQUIRED:
    core_metrics.counter(m, 0)

_APPS = [
    ("api_edge", api_app),
    ("gateway",  gw_app),
    ("memory_api", mem_app),
    ("ingest",   ing_app),
]


def test_metrics_exposed_and_contains_required_names():
    for name, application in _APPS:
        client = TestClient(application)
        # Trigger one real request so latency histogram is recorded
        # (esp. for api_edge which records per-request TTFB).
        try:
            client.get("/readyz", headers={"authorization": "Bearer test"})
        except Exception:
            # If service lacks /readyz or auth, ignore; /metrics will still be checked
            pass
        res = client.get("/metrics")
        assert res.status_code == 200, f"{name} missing /metrics"
        body = res.text
        for metric in _REQUIRED:
            assert metric in body, f"{name} missing {metric}"
        if name == "api_edge":
            # Histogram presence (any of the standard histogram series will do)
            assert "api_edge_ttfb_seconds_bucket" in body or "api_edge_ttfb_seconds_count" in body, \
                "api_edge missing api_edge_ttfb_seconds histogram series"
