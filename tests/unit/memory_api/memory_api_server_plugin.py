"""
Real Memory-API server fixture (Uvicorn) – shared by *all* tests.

• Starts exactly once per test session.
• Re-uses an already-running Memory-API on the chosen port when possible.
• Monkey-patch helper keeps legacy tests that overwrite `memory_api.app.store`
  from blowing up.
"""
from __future__ import annotations

import os, sys, socket, threading, time, logging
from contextlib import suppress
from pathlib import Path

import httpx, pytest, uvicorn

# ── make internal packages importable ───────────────────────────────────────
ROOT = Path(__file__).resolve().parents[3]            # repo root
sys.path.insert(0, str(ROOT / "services" / "memory_api" / "src"))
for src in (ROOT / "packages").glob("*/src"):
    sys.path.insert(0, str(src))

from memory_api.app import app          # noqa: E402 – after sys.path tweaks

API_HOST = "127.0.0.1"
def _pick_free_port() -> int:
    """Return an OS-allocated free TCP port (race-free)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

# Honour caller preference but fall back to a random free port.
_env_port = os.getenv("MEMORY_API_TEST_PORT")
API_PORT   = int(_env_port) if _env_port and _env_port != "0" else _pick_free_port()

# Export so helpers (and FastAPI clients inside tests) can discover the base-url
os.environ["MEMORY_API_TEST_PORT"] = str(API_PORT)
os.environ.setdefault("MEMORY_API_BASE", f"http://memory_api:{API_PORT}")

# ---------------------------------------------------------------------------#
#  DNS override – “memory_api” → 127.0.0.1                                   #
# ---------------------------------------------------------------------------#
def _patch_dns() -> None:
    original = socket.getaddrinfo
    def _resolver(host: str, *a, **k):        # type: ignore[override]
        if host == "memory_api":
            host = API_HOST
        return original(host, *a, **k)
    socket.getaddrinfo = _resolver            # type: ignore[assignment]

# ---------------------------------------------------------------------------#
#  Helpers to probe existing process                                         #
# ---------------------------------------------------------------------------#
def _is_port_open(host: str, port: int) -> bool:
    with suppress(OSError):
        with socket.create_connection((host, port), timeout=0.2):
            return True
    return False

def _is_our_api(host: str, port: int) -> bool:
    try:
        with httpx.Client(timeout=0.5) as c:
            r = c.get(f"http://{host}:{port}/healthz")
            if r.status_code != 200 or r.json().get("status") != "ok":
                return False
            r = c.post(f"http://{host}:{port}/api/resolve/text", json={})
            return r.status_code == 200 and "matches" in r.json()
    except Exception:
        return False

# ---------------------------------------------------------------------------#
#  Session-wide Uvicorn server                                               #
# ---------------------------------------------------------------------------#
@pytest.fixture(scope="session", autouse=True)
def memory_api_server() -> None:
    """Launch (or reuse) the real Memory-API for the whole test session."""
    _patch_dns()

    if _is_port_open(API_HOST, API_PORT) and _is_our_api(API_HOST, API_PORT):
        logging.info("re-using Memory-API on %s:%s", API_HOST, API_PORT)
        yield
        return

    cfg   = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="warning", lifespan="on")
    srv   = uvicorn.Server(cfg)
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            srv.run()
        except BaseException as exc:           # pragma: no cover
            error.append(exc)
            raise

    th = threading.Thread(target=_runner, daemon=True)
    th.start()

    # Allow slower CI boxes or first-time cold-starts to boot.
    startup_timeout = float(os.getenv("MEMORY_API_STARTUP_TIMEOUT", "30"))
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if _is_port_open(API_HOST, API_PORT) and _is_our_api(API_HOST, API_PORT):
            break
        if error:
            raise RuntimeError("Memory-API crashed on start-up") from error[0]
        time.sleep(0.1)
    else:
        if error:
            raise RuntimeError("Memory-API crashed on start-up") from error[0]
        logging.warning(
            "Memory-API failed to boot within %.1fs – "
            "continuing test-session with httpx stub only", startup_timeout
            )
        yield
        return

    yield
    srv.should_exit = True
    th.join(timeout=5)

# ---------------------------------------------------------------------------#
#  Legacy helper – guarantee `.cache_clear()`                                #
# ---------------------------------------------------------------------------#
@pytest.fixture(autouse=True)
def guard_store_cache_clear(monkeypatch):
    """Attach a no-op `.cache_clear` when tests monkey-patch `memory_api.app.store`."""
    import memory_api.app as mem_app                    # local import
    original = monkeypatch.setattr

    def _patched(*args, **kwargs):                      # type: ignore[override]
        original(*args, **kwargs)
        if len(args) >= 3 and not isinstance(args[0], str):
            tgt, name, val = args[:3]
            if tgt is mem_app and name == "store" and not hasattr(val, "cache_clear"):
                setattr(val, "cache_clear", lambda: None)
    monkeypatch.setattr = _patched
    yield
    monkeypatch.setattr = original
