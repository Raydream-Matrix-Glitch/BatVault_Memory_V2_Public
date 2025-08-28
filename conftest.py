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
import os, sys

# ── Environment glue ────────────────────────────────────────────────────
# The autouse Memory-API plugin chooses a random free port and exports it
# via MEMORY_API_TEST_PORT.  Down-stream services (the Gateway) discover the
# Memory-API through the canonical MEMORY_API_URL, so we mirror the port
# *before* those services are imported.
_mem_port = os.getenv("MEMORY_API_TEST_PORT")
if _mem_port:
    os.environ.setdefault("MEMORY_API_URL", f"http://memory_api:{_mem_port}")

# ── 1 · add all source roots to PYTHONPATH (prepend so we win over site-packages) ─
ROOT = Path(__file__).parent.resolve()
_paths = (
    [str(ROOT)]                                           # project root
    + [str(p) for p in (ROOT / "packages").glob("*/src")] # packages/*/src
    + [str(p) for p in (ROOT / "services").glob("*/src")] # services/*/src
)
# Preserve order but ensure local paths take precedence
for _p in reversed(_paths):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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

# ── Real HTTP base-URL for performance tests ────────────────────────────
@pytest.fixture(scope="session")
def gw_url(unused_tcp_port_factory):
    """
    Provide a *real* Gateway URL.

    • Honour GW_URL / GATEWAY_BASE_URL when they’re set.  
    • Otherwise start Uvicorn in-process on a free port.
    """
    import threading, socket, time, uvicorn

    explicit = (os.getenv("GW_URL") or os.getenv("GATEWAY_BASE_URL") or "").rstrip("/")
    if explicit:
        yield explicit
        return

    # Ensure Gateway sees the live Memory-API port
    mem_port = os.getenv("MEMORY_API_TEST_PORT", "8000")
    os.environ.setdefault("MEMORY_API_URL", f"http://memory_api:{mem_port}")

    # Spin up Gateway server
    from services.gateway.src.gateway.app import app as gw_app

    host = "127.0.0.1"
    port = unused_tcp_port_factory()
    cfg  = uvicorn.Config(gw_app, host=host, port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(cfg)

    th = threading.Thread(target=server.run, daemon=True, name="gateway-uvicorn")
    th.start()

    # Wait (≤3 s) until the socket accepts connections
    for _ in range(30):
        with socket.socket() as s:
            if s.connect_ex((host, port)) == 0:
                break
            time.sleep(0.1)
    else:
        raise RuntimeError("Gateway server failed to start")

    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        th.join(timeout=5)
