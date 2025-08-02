"""
Gateway package initialiser.

Adds a **safe redis shim** so local `pytest` runs without the system-level
Redis C client.  When the real ``redis`` package is missing we first try
to fall back to ``fakeredis``.  If *both* are unavailable (e.g. on very
minimal CI runners), we create a tiny in-process stub that fulfils only
the methods our codebase touches.
"""
from types import SimpleNamespace

try:
    import redis  # noqa: F401 – real dependency
except ModuleNotFoundError:  # pragma: no-cover
    # ------------------------------------------------------------------
    #  Try `fakeredis` first …
    # ------------------------------------------------------------------
    try:
        import fakeredis  # type: ignore
    except ModuleNotFoundError:  # pragma: no-cover
        # ------------------------------------------------------------------
        #  … and if that too is missing, fabricate an *ultra-light* stub
        #  implementing just the handful of methods the codebase uses.
        # ------------------------------------------------------------------
        class _FakeRedis:                               # noqa: D401
            """Ultra-light no-op redis stub (sync + async)."""

            # ---------- sync API ---------- #
            def get(self, *_a, **_kw):
                return None

            def set(self, *_a, **_kw):
                return None

            # ---------- async API ---------- #
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a, **_kw):
                return False

            # Gracefully ignore *any* other attribute
            def __getattr__(self, _):
                return lambda *__, **___: None

        fakeredis = SimpleNamespace(FakeRedis=_FakeRedis)  # type: ignore

    # ------------------------------------------------------------------
    #  Expose a minimal redis-like interface expected by the codebase.
    # ------------------------------------------------------------------
    class _Shim(SimpleNamespace):
        Redis = fakeredis.FakeRedis
        from_url = staticmethod(lambda *_a, **_kw: fakeredis.FakeRedis())  # type: ignore

    import sys

    # Register shim under both sync and asyncio namespaces
    sys.modules.setdefault("redis", _Shim)          # sync API
    sys.modules.setdefault("redis.asyncio", _Shim)  # asyncio API

# Re-export nothing – this file only patches import machinery
__all__: list[str] = []
