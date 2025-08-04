"""
Verifies the token-bucket middleware on api_edge.

ENV overrides must be set *before* the FastAPI app is imported.
"""

import os, time, importlib
from fastapi.testclient import TestClient

# one window = 2 requests / second
os.environ["API_RATE_LIMIT_DEFAULT"] = "2/second"

# defer import until env is set
from services.api_edge import app as api_edge_app  # noqa: E402

# reload to pick up new env in dev runs
importlib.reload(api_edge_app)

client = TestClient(
    api_edge_app.app if hasattr(api_edge_app, "app") else api_edge_app
)


def test_token_bucket_2_per_second():
    # first window – 3 hits in quick succession
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").status_code == 429  # over the limit

    # next window – should reset
    time.sleep(1.1)
    assert client.get("/healthz").status_code == 200
