from __future__ import annotations

# ─── ensure the Memory-API package in src/ is on PYTHONPATH ───
import os, sys

# conftest is in services/memory_api/tests
_C = os.path.dirname(__file__)
# move up to services/memory_api
_API_ROOT = os.path.abspath(os.path.join(_C, ".."))
# point at the src/ folder where memory_api lives
_SRC = os.path.join(_API_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import socket
import threading
import time
from contextlib import suppress

import pytest
import uvicorn

# FastAPI app exported by the package
from memory_api.app import app

API_HOST = "127.0.0.1"
API_PORT = int(os.getenv("MEMORY_API_TEST_PORT", "8000"))

# Ensure tests use the correct base URL **before** they’re imported
os.environ.setdefault("MEMORY_API_BASE", f"http://memory_api:{API_PORT}")


# ---------------------------------------------------------------------------#
# DNS helper: map `memory_api` → 127.0.0.1 (local-only)
# ---------------------------------------------------------------------------#
def _patch_dns() -> None:
    original_getaddrinfo = socket.getaddrinfo

    def _resolver(host: str, *args, **kwargs):  # type: ignore[override]
        if host == "memory_api":
            host = API_HOST
        return original_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _resolver  # type: ignore[assignment]


# ---------------------------------------------------------------------------#
# Session-wide fixture: run Memory-API & clean up
# ---------------------------------------------------------------------------#
@pytest.fixture(scope="session", autouse=True)
def _memory_api_server():
    """Start Memory-API once for the whole test session."""

    _patch_dns()

    config = uvicorn.Config(
        app,
        host=API_HOST,
        port=API_PORT,
        lifespan="on",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="memory-api")
    thread.start()

    # Wait until the socket is accepting connections (max ~5 s)
    for _ in range(50):
        with suppress(OSError):
            with socket.create_connection((API_HOST, API_PORT), timeout=0.1):
                break
        time.sleep(0.1)
    else:
        raise RuntimeError("Memory-API test server failed to start")

    yield  # ---- run the entire test session ----

    server.should_exit = True
    thread.join(timeout=5)