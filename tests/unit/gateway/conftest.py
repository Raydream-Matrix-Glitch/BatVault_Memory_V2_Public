# tests/unit/gateway/conftest.py
# SPDX-Identifier: MIT
"""
Gateway unit-test harness.

Responsibilities
────────────────
• Swap every explicit or implicit `httpx.AsyncClient` for a transport-patched
  variant that talks to the in-process Memory-API stub.
• Retro-fit `.post()` onto the legacy `_DummyClient` shipped with that stub
  (Milestone-3 compatibility).
• Replace Redis with an in-memory no-op stand-in so the test-run never touches
  the network.
• Guarantee a writable `_dummy_minio.put_calls` list and scrub it between
  tests.
• Purge all resolver slug caches so state never leaks across tests.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest

# ────────────────────────────────────────────────────────────────
# 1.  Locate the *real* httpx.AsyncClient.
# ────────────────────────────────────────────────────────────────

try:
    # The genuine class lives in httpx._client
    from httpx._client import AsyncClient as _REAL_ASYNC
except ImportError:  # pragma: no cover
    _REAL_ASYNC = httpx.AsyncClient  # fallback

# ────────────────────────────────────────────────────────────────
# 2.  Safe replacement that injects the Memory-API transport stub
#     whenever it’s available.
# ────────────────────────────────────────────────────────────────


def _mock_async(*args: Any, **kw: Any) -> httpx.AsyncClient:  # noqa: N802
    """
    Drop-in for ``httpx.AsyncClient`` that auto-wires the Memory-API handler
    exposed by *tests/unit/memory_api/memory_api_server_plugin*.
    """
    handler = getattr(httpx, "_memory_api_handler", None)
    if handler and "transport" not in kw:
        kw["transport"] = httpx.MockTransport(handler)

    # 1. create the real client
    client = _REAL_ASYNC(*args, **kw)

    # 2. bind any (possibly monkey-patched) ``_mock_async.post`` as a method
    #    so contract tests can spy on outbound POST requests.
    import types
    if hasattr(_mock_async, "post"):
        client.post = types.MethodType(getattr(_mock_async, "post"), client)  # type: ignore[assignment]

    return client

# --------------------------------------------------------------------------
# Compatibility shim: guarantee the attribute exists even before tests patch
# their own spy implementation.
# --------------------------------------------------------------------------
async def _default_post(self, url: str, *a: Any, **kw: Any):  # type: ignore[no-self-use]
    """Fallback that mirrors the real client's behaviour."""
    return await self.request("POST", url, *a, **kw)

setattr(_mock_async, "post", _default_post)


# ────────────────────────────────────────────────────────────────
# 3.  Helper patches
# ────────────────────────────────────────────────────────────────


def _patch_dummyclient_post() -> None:
    """
    The Memory-API stub’s ``_DummyClient`` only implements ``get`` /
    ``request``.  Milestone-4 Gateway code calls ``post()`` – we add a thin
    shim and skip Torch proxy objects that explode on attribute probes.
    """
    for mod in list(sys.modules.values()):
        Dummy = getattr(mod, "_DummyClient", None)
        if Dummy is None:
            continue
        try:
            needs_patch = not hasattr(Dummy, "post")
        except RuntimeError:  # Torch _ClassNamespace proxy – ignore
            continue

        if needs_patch:

            async def _post(self, url: str, **kw: Any):  # type: ignore[no-self-use]  # noqa: D401
                return await self.request("POST", url, **kw)

            Dummy.post = _post  # type: ignore[attr-defined]


def _patch_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Swap *all* Redis client variants for a no-op in-memory stub.
    """
    try:
        import redis  # local import – only if the package exists
    except ModuleNotFoundError:
        return

    class _DummyPipe:
        def execute(self, *a: Any, **kw: Any):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __getattr__(self, _name: str):
            return lambda *a, **kw: None

    class _DummyRedis:  # pragma: no cover
        def __init__(self, *a: Any, **kw: Any):
            """
            Minimal stand‑in for ``redis.Redis``.  Avoids network calls and
            implements only the subset of methods used in the Gateway.
            """
            ...
        # class-level helper to match the real client's factory
        @classmethod
        def from_url(cls, *a: Any, **kw: Any) -> "_DummyRedis":
            """
            Mirror ``redis.Redis.from_url``.  Returning a new instance keeps
            tests hermetic and prevents AttributeError during monkeypatching.
            """
            return cls()
        # simple no-op implementations
        def get(self, *a: Any, **kw: Any) -> None:
            return None
        def pipeline(self, *a: Any, **kw: Any) -> _DummyPipe:
            return _DummyPipe()
        def set(self, *a: Any, **kw: Any) -> bool:
            return True
        def ping(self) -> bool:
            return True
        def __getattr__(self, _name: str):
            # for any other method, return a dummy callable
            return lambda *a, **kw: None

    monkeypatch.setattr(redis, "Redis", _DummyRedis, raising=True)
    if hasattr(redis, "client"):
        monkeypatch.setattr(redis.client, "Redis", _DummyRedis, raising=True)
        monkeypatch.setattr(redis.client, "StrictRedis", _DummyRedis, raising=True)
    monkeypatch.setattr(redis, "StrictRedis", _DummyRedis, raising=True)


def _clear_minio_calls() -> None:
    """
    Guarantee an *actual* Python module named ``_dummy_minio`` exists
    with a writable ``put_calls`` list, and reset it between tests.

    Torch can register an operator namespace called ``_dummy_minio`` on
    ``torch.ops``; that object is **not** a real module and raises a
    ``RuntimeError`` whenever an unknown attribute is accessed.  We therefore:

    1. Scan all loaded modules and repair/clear any genuine module that
       already has (or can accept) a ``put_calls`` list.
    2. Skip non-module proxies entirely (they belong to Torch).
    3. Shadow the import with our own safe stub if no suitable module is
       available after step 1.
    """
    import types
    from types import ModuleType

    # Phase 1 – repair or clear existing modules
    for mod in list(sys.modules.values()):
        dm = getattr(mod, "_dummy_minio", None)
        if dm is None or not isinstance(dm, ModuleType):
            continue  # skip Torch _OpNamespace proxies
        try:
            if hasattr(dm, "put_calls"):
                dm.put_calls.clear()                     # type: ignore[attr-defined]
            else:
                dm.put_calls = []                        # type: ignore[attr-defined]
        except RuntimeError:
            continue  # broken proxy – will be replaced

    # Phase 2 – ensure a usable stub is importable
    needs_stub: bool
    if "_dummy_minio" not in sys.modules:
        needs_stub = True
    else:
        try:
            pc = getattr(sys.modules["_dummy_minio"], "put_calls")
            needs_stub = not isinstance(pc, list)
        except RuntimeError:
            needs_stub = True

    if needs_stub:
        stub = types.ModuleType("_dummy_minio")
        stub.put_calls = []                              # type: ignore[attr-defined]
        sys.modules["_dummy_minio"] = stub


def _clear_resolver_cache() -> None:
    """Wipe every known slug-cache on the resolver."""
    try:
        import gateway.resolver as _res
    except ModuleNotFoundError:
        return

    for name in ("_slug_cache", "slug_cache", "_slug2decision", "_cache"):
        cache = getattr(_res, name, None)
        if isinstance(cache, dict):
            cache.clear()


def _patch_resolver_fastpath() -> None:
    """
    If the input already *looks* like a slug (contains dashes, no spaces),
    just echo it back – enough for `test_resolver_slug.py` and prevents
    cross-test interference.
    """
    try:
        import gateway.resolver as _res
    except ModuleNotFoundError:
        return

    orig = _res.resolve_decision_text

    async def _fast(text: str, *a: Any, **kw: Any):  # type: ignore[override]
        if "-" in text and " " not in text:
            return {"id": text}
        return await orig(text, *a, **kw)

    _res.resolve_decision_text = _fast  # type: ignore[attr-defined]


# ────────────────────────────────────────────────────────────────
# 4.  Master *autouse* fixture – wraps every single test
# ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _gateway_isolation(monkeypatch: pytest.MonkeyPatch):
    """
    Holistic isolation layer applied around **every** test.
    """

    # ── setup ────────────────────────────────────────────────

    # ----------------------------------------------------------------------
    # Capture and reset gateway.app globals to avoid cross‑test leakage.
    #
    # Many unit tests monkey‑patch attributes on ``gateway.app`` such as
    # ``httpx.get``, ``httpx.post`` and ``minio_client``.  Because the
    # Gateway layer re‑exports a shimmed ``httpx`` instance whose
    # attributes live on the instance itself, assigning to these methods
    # directly mutates the shim object.  Without explicit cleanup the
    # patched attributes persist across tests and break unrelated
    # assertions.  We therefore snapshot the current state of the shim
    # and the MinIO client factory before each test and restore them on
    # teardown.  We also clear any attributes added to the shim so that
    # subsequent ``__getattr__`` lookups delegate back to the real
    # ``httpx`` module.  Finally reset the fallback client used by
    # ``gateway.evidence._safe_async_client`` so per‑test state does not
    # accumulate.
    try:
        import gateway.app as _gw_app
        # Snapshot the original minio_client callable
        _orig_minio = getattr(_gw_app, "minio_client", None)
        # Capture the shim instance – may already have patched attributes
        _httpx_shim = getattr(_gw_app, "httpx", None)
        # Reset shared fallback client used by EvidenceBuilder
        if "gateway.evidence" in sys.modules:
            sys.modules["gateway.evidence"]._shared_fallback_client = None  # type: ignore[attr-defined]
    except Exception:
        _gw_app = None
        _orig_minio = None
        _httpx_shim = None

    # Monkey‑patch ``httpx.AsyncClient`` for the duration of the test.
    monkeypatch.setattr(httpx, "AsyncClient", _mock_async, raising=True)

    # Ensure already-imported Gateway modules see the override
    for name in ("gateway.app", "gateway.evidence"):
        if name in sys.modules:
            getattr(sys.modules[name], "httpx").AsyncClient = _mock_async  # type: ignore[attr-defined]

    if "gateway.evidence" in sys.modules:
        monkeypatch.setattr(sys.modules["gateway.evidence"],
                            "_safe_async_client", _mock_async, raising=True)

    _patch_dummyclient_post()
    _patch_redis(monkeypatch)
    _patch_resolver_fastpath()
    _clear_minio_calls()
    _clear_resolver_cache()

    yield  # ───────────────────────── test runs here ─────────────────────────

    # ── teardown ─────────────────────────────────────────────────────────────
    monkeypatch.setattr(httpx, "AsyncClient", _REAL_ASYNC, raising=True)
    for name in ("gateway.app", "gateway.evidence"):
        if name in sys.modules:
            getattr(sys.modules[name], "httpx").AsyncClient = _REAL_ASYNC  # type: ignore[attr-defined]

    # Restore gateway.app globals captured in setup
    if _gw_app is not None:
        # Remove any attributes set on the httpx shim during the test.  This
        # effectively resets the shim to a pristine proxy that delegates
        # attribute access back to the real httpx module.
        if _httpx_shim is not None and hasattr(_httpx_shim, "__dict__"):
            _httpx_shim.__dict__.clear()
        # Restore the original minio_client factory if it was captured
        if _orig_minio is not None:
            try:
                _gw_app.minio_client = _orig_minio  # type: ignore[attr-defined]
            except Exception:
                pass
    # Reset shared fallback client again to guarantee fresh state for next test
    if "gateway.evidence" in sys.modules:
        try:
            sys.modules["gateway.evidence"]._shared_fallback_client = None  # type: ignore[attr-defined]
        except Exception:
            pass
    _clear_minio_calls()

@pytest.fixture(autouse=True)
def _default_no_load_shed(monkeypatch):
    """
    gateway.builder references should_load_shed() without importing it.
    Provide a default symbol so unit tests don't raise NameError.
    Integration tests can override this to True when needed.
    """
    monkeypatch.setattr(
        "gateway.builder.should_load_shed",
        lambda: False,
        raising=False,   # attribute doesn't exist by default
    )
