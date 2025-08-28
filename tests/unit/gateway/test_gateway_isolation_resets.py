"""Unit tests verifying that the autouse gateway isolation fixture resets
test-specific patches on ``gateway.app`` between tests.

These tests intentionally mutate ``gateway.app.httpx`` and ``gateway.app.minio_client``
to simulate behaviour in other unit tests.  The second test asserts that
the mutations made in the first test do not persist into subsequent tests.

These checks guard against cross-test leakage of patched methods and ensure
that the isolation layer implemented in ``tests/unit/gateway/conftest.py``
correctly restores the original state of the HTTPX shim and MinIO client
factory after each test.
"""

import gateway.app as gw_app


def test_gateway_isolation_patches_do_not_leak_part1() -> None:
    """Patch httpx shim and minio_client for the duration of this test.

    Assign a dummy attribute on the gateway.app.httpx shim and replace
    minio_client with a lambda.  The autouse isolation fixture should
    preserve this state only within this test.
    """
    # Ensure attribute does not exist prior to patch
    assert not hasattr(gw_app.httpx, "_leak_marker"), "precondition failed"
    # Patch the shim and minio_client
    gw_app.httpx._leak_marker = lambda: True  # type: ignore[attr-defined]
    gw_app.minio_client = lambda: "dummy"  # type: ignore[assignment]
    # Confirm patches are visible within this test
    assert callable(gw_app.httpx._leak_marker)  # type: ignore[attr-defined]
    assert gw_app.minio_client() == "dummy"


def test_gateway_isolation_patches_do_not_leak_part2() -> None:
    """Verify that patches applied in the previous test are cleared.

    After the autouse isolation fixture runs, the dummy attribute and
    overridden minio_client should have been removed.  The httpx shim
    should no longer expose ``_leak_marker`` and minio_client should not
    return the dummy sentinel.
    """
    # The marker attribute should have been removed by the isolation fixture
    assert not hasattr(gw_app.httpx, "_leak_marker"), (
        "gateway.app.httpx leaked a patched attribute across tests"
    )
    # The minio_client should not be our dummy lambda any more
    # We don't assert the exact type of the returned object (it depends on
    # environment configuration), but it must not equal the sentinel value
    assert gw_app.minio_client() != "dummy"