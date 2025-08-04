import os, time, uuid, asyncio, logging, httpx, anyio, inspect
import pytest
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------- #
# Extra fixture required by test_gateway_metric_names_present                 #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def test_client_gateway():
    """
    Simple TestClient over the real Gateway ASGI app – used by metrics tests.
    """
    from gateway.app import app as gateway_app

    return TestClient(gateway_app)

_DEFAULT = os.getenv("GATEWAY_URL", "http://localhost:8000")

# ───────────────────────────────────────────────────────────────
#  Mini-plugin so plain `async def` tests run even without
#  pytest-asyncio installed in the CI venv (Milestone-3 requirement)
# ───────────────────────────────────────────────────────────────

def pytest_pyfunc_call(pyfuncitem):
    test_func = pyfuncitem.obj
    if inspect.iscoroutinefunction(test_func):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(test_func(**pyfuncitem.funcargs))
        return True
    return None


async def _wait(url: str, timeout: float = 60.0, interval: float = 1.5) -> None:
    """
    Non-blocking readiness probe executed inside the pytest event-loop.
    """
    log = logging.getLogger("tests._wait")
    start = time.time()

    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() - start < timeout:
            try:
                if (await client.get(url)).status_code == 200:
                    log.info(
                        "gateway_ready",
                        extra={
                            "stage": "pytest_setup",
                            "request_id": str(uuid.uuid4()),
                            "target": url,
                        },
                    )
                    return
            except httpx.RequestError:
                pass
            await asyncio.sleep(interval)

    raise RuntimeError(f"Timed-out waiting for {url}")

@pytest.fixture(scope="session")
def api_ready() -> None:
    """Opt-in fixture; import only in tests that hit the HTTP gateway."""
    anyio.run(_wait, f"{_DEFAULT}/readyz")

@pytest.fixture(scope="session")
def gw_url():
    return _DEFAULT

# ------------------------------------------------------------------ #
#  Local fixture for API-Edge client (required by new metrics suite)
# ------------------------------------------------------------------ #
import pytest
from fastapi.testclient import TestClient
from services.api_edge import app as _api_mod

@pytest.fixture()
def test_client_api_edge():
    """Return a fresh FastAPI TestClient for API-Edge."""
    return TestClient(_api_mod.app if hasattr(_api_mod, "app") else _api_mod)
