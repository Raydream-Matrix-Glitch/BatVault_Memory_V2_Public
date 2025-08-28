"""
Verifies the token-bucket middleware on api_edge.

ENV overrides must be set *before* the FastAPI app is imported.
"""

import importlib
import os
import time
from typing import Callable

from fastapi.testclient import TestClient
from requests import Response

# one window = 2 requests / second
os.environ["API_RATE_LIMIT_DEFAULT"] = "2/second"

# defer import until env is set
from services.api_edge import app as api_edge_app  # noqa: E402

# reload to pick up new env in dev runs
importlib.reload(api_edge_app)

client = TestClient(api_edge_app.app if hasattr(api_edge_app, "app") else api_edge_app)

ROUTE = "/ratelimit-test"


def _hit() -> Response:
    return client.get(ROUTE)


def test_token_bucket_2_per_second():
    # first window – 3 hits in <1 s (capacity = 2 → third must overflow)
    assert _hit().status_code == 200
    assert _hit().status_code == 200
    assert _hit().status_code == 429, "3rd hit should exceed the 2-req/sec bucket"

    # next window – bucket refills after 1 s
    time.sleep(1.1)
    assert _hit().status_code == 200, "Bucket must refill after 1 s"

    # sanity-check: health & readiness probes stay UN-throttled
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
