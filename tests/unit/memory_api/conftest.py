from __future__ import annotations

# ─── ensure the Memory-API package in src/ is on PYTHONPATH ───
import os, sys
from types import FunctionType

# conftest is in services/memory_api/tests
_C = os.path.dirname(__file__)
# move up to services/memory_api
_API_ROOT = os.path.abspath(os.path.join(_C, ".."))
# point at the src/ folder where memory_api lives
_SRC = os.path.join(_API_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import socket
import json
import logging
import threading
import time
from contextlib import suppress
import pytest
import uvicorn
import httpx

# FastAPI app exported by the package
from memory_api.app import app

API_HOST = "127.0.0.1"
API_PORT = int(os.getenv("MEMORY_API_TEST_PORT", "8000"))

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
def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False

def _is_our_memory_api(host: str, port: int, timeout: float = 0.5) -> bool:
    """
    Return True iff a Memory API compatible server is listening on host:port.
    Probe /healthz and /api/resolve/text to avoid reusing an unrelated process.
    """
    base = f"http://{host}:{port}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base}/healthz")
            if r.status_code != 200 or r.json().get("status") != "ok":
                return False
            r = client.post(f"{base}/api/resolve/text", json={})
            if r.status_code != 200:
                return False
            body = r.json()
            return "matches" in body and "query" in body
    except Exception:
        return False
    
def _find_free_port(host: str = API_HOST) -> int:
    """Ask the OS for an available ephemeral port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()

@pytest.fixture(scope="session", autouse=True)
def _memory_api_server():
    """Start Memory-API once for the whole test session."""
    global API_PORT
    _patch_dns()

    # If the port is already in use, only reuse it if it looks like *our* Memory API.
    if _is_port_open(API_HOST, API_PORT):
        if _is_our_memory_api(API_HOST, API_PORT):
            logging.info("event=memory_api_server_reuse host=%s port=%s", API_HOST, API_PORT)
            # Ensure env BASE matches what we reuse
            os.environ["MEMORY_API_BASE"] = f"http://memory_api:{API_PORT}"
            yield
            return
        else:
            # Choose an alternate free port instead of failing fast.
            new_port = _find_free_port(API_HOST)
            logging.info(
                "event=memory_api_server_bind_alt host=%s preferred_port=%s chosen_port=%s reason=occupied_by_other",
                API_HOST, API_PORT, new_port
            )
            API_PORT = new_port

    # Always publish the base URL for the port we will use
    os.environ["MEMORY_API_BASE"] = f"http://memory_api:{API_PORT}"

    config = uvicorn.Config(
        app,
        host=API_HOST,
        port=API_PORT,
        lifespan="on",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to accept connections *and* pass the identity probe
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _is_port_open(API_HOST, API_PORT) and _is_our_memory_api(API_HOST, API_PORT):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Memory-API server failed to start for tests")

    yield  # ---- run the entire test session ----

    server.should_exit = True
    thread.join(timeout=5)

# ---------------------------------------------------------------------------#
#  Autouse fixture: ensure monkey-patched *store* objects still expose       #
#  `.cache_clear()` so legacy tests don’t crash with AttributeError.        #
# ---------------------------------------------------------------------------#

@pytest.fixture(autouse=True)
def _guard_store_cache_clear(monkeypatch):
    """
    Legacy unit tests replace ``memory_api.app.store`` with a plain lambda.
    That lambda lacks the ``cache_clear`` attribute that production code
    (and some tests!) expect.  We wrap *monkeypatch.setattr* so every time
    the symbol is replaced we transparently attach a no-op ``cache_clear``.
    """
    import memory_api.app as mem_app  # local import avoids circular refs

    original_setattr = monkeypatch.setattr
    def _patched(*args, **kwargs):  # type: ignore[override]
        """Support both monkeypatch.setattr(target,name,value) and monkeypatch.setattr("dotted.path", value)."""
        original_setattr(*args, **kwargs)
        if len(args) >= 3 and not isinstance(args[0], str):
            target, name, value = args[:3]
            if target is mem_app and name == "store" and not hasattr(value, "cache_clear"):
                setattr(value, "cache_clear", lambda: None)

    monkeypatch.setattr = _patched
    yield
    monkeypatch.setattr = original_setattr