"""
Global conftest for BatVault tests.

This file combines:
1. A pretty unified-diff assertion helper for clearer dict-vs-dict failures.
2. An autouse fixture that resets any monkey-patches applied to
   `gateway.app.httpx` during gateway-unit tests, preventing state leakage
   when the entire suite is run in one process.
"""

import json
import difflib
from types import SimpleNamespace

import pytest

# --------------------------------------------------------------------------- #
# Pretty diff for dict comparisons                                            #
# --------------------------------------------------------------------------- #
def pytest_assertrepr_compare(op, left, right):
    """Pretty unified-diff output when comparing two dicts with ==."""
    if isinstance(left, dict) and isinstance(right, dict) and op == "==":
        lhs = json.dumps(left, indent=2, sort_keys=True).splitlines()
        rhs = json.dumps(right, indent=2, sort_keys=True).splitlines()
        return [""] + list(
            difflib.unified_diff(lhs, rhs, fromfile="left", tofile="right")
        )


# --------------------------------------------------------------------------- #
# Isolation fixture for gateway.httpx monkey-patches                          #
# --------------------------------------------------------------------------- #
try:
    import gateway.app as gw_app
except ModuleNotFoundError:
    gw_app = None  # Allows non-gateway test subsets to run without gateway

if gw_app is not None and hasattr(gw_app, "httpx"):
    _HTTPX_BASELINE = SimpleNamespace(
        get=getattr(gw_app.httpx, "get", None),
        post=getattr(gw_app.httpx, "post", None),
        AsyncClient=getattr(gw_app.httpx, "AsyncClient", None),
    )

    @pytest.fixture(autouse=True)
    def _isolate_httpx_mocks():
        """
        Restore the original `gateway.app.httpx` attributes after each test.

        Several gateway unit-test modules monkey-patch these symbols at *import*
        time. When the whole suite runs in a single Python process those
        patches bleed into later tests and cause failures. This fixture resets
        them after every test function to guarantee independence.
        """
        yield

        # Idempotently restore only attributes that actually exist
        if _HTTPX_BASELINE.get is not None:
            gw_app.httpx.get = _HTTPX_BASELINE.get
        if _HTTPX_BASELINE.post is not None:
            gw_app.httpx.post = _HTTPX_BASELINE.post
        if _HTTPX_BASELINE.AsyncClient is not None:
            gw_app.httpx.AsyncClient = _HTTPX_BASELINE.AsyncClient
