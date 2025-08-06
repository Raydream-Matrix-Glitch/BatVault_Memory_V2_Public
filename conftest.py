"""
Project-wide PyTest bootstrap – **integration stack**

Responsibilities
────────────────
1.  Put every `*/src` directory on PYTHONPATH so tests can import the
    project’s packages without editable installs.
2.  Expose the Memory-API server fixture (defined once inside
    `tests/unit/memory_api/conftest.py`) to the entire test-suite
    via `pytest_plugins`.
"""

from pathlib import Path
import sys

# ── 1 · add all source roots to PYTHONPATH ─────────────────────────────────
ROOT = Path(__file__).parent.resolve()

sys.path.extend(
    [str(ROOT)]                                           # project root
    + [str(p) for p in (ROOT / "packages").glob("*/src")] # packages/*/src
    + [str(p) for p in (ROOT / "services").glob("*/src")] # services/*/src
)

def _try_import(pm, name: str):
    try:
        pm.import_plugin(name)
    except ModuleNotFoundError:
        pass

def pytest_configure(config):
    pm = config.pluginmanager
    for _p in (
        "tests.unit.memory_api.memory_api_server_plugin",  # real Memory-API
        "pytest_asyncio", "pytest_env",                    # nice-to-have
    ):
        _try_import(pm, _p)

#     Fail early with a clear, readable message if a developer forgets the
#     dependency pin.
for _plugin in ("pytest_asyncio", "pytest_env"):
    try:
        __import__(_plugin)
    except ImportError as exc:
        raise RuntimeError(
            f"{_plugin} is required for async tests – "
            "add it to requirements/dev.txt."
        ) from exc


# ── 3 · shared FastAPI TestClient fixtures (Milestone-3) ───────────────────
#      These run **once per session** to avoid Prometheus collector
#      duplication warnings and to shave ~250 ms off the suite.
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def test_client_api_edge():
    """Singleton client for the **API-Edge** service."""
    from services.api_edge.src.api_edge.app import app as api_app
    # context-managed => lifespan (= startup/shutdown) events fire
    with TestClient(api_app) as client:
        yield client


@pytest.fixture(scope="session")
def test_client_gateway():
    """Singleton client for the **Gateway** service (graph expansion, etc.)."""
    from services.gateway.src.gateway.app import app as gw_app
    with TestClient(gw_app) as client:
        yield client
