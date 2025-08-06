# tests/helpers/memory_api_stub.py
from contextlib import contextmanager
import httpx
from httpx import MockTransport


def _build_transport(anchor_id: str, event_id: str):
    """
    Build a MockTransport *and* surface its underlying handler so that tests
    can assert against it (e.g. via `httpx._memory_api_handler`).
    """
    def _handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/graph/expand_candidates" and req.method == "POST":
            return httpx.Response(
                200,
                json={
                    "neighbors": [
                        {"type": "event", "id": event_id,  "score": 0.99},
                        {"type": "decision", "id": anchor_id, "score": 1.0},
                    ]
                },
            )

        if p == f"/api/enrich/decision/{anchor_id}" and req.method == "GET":
            return httpx.Response(
                200,
                json={"id": anchor_id, "supported_by": [event_id]},
            )

        if p == f"/api/enrich/event/{event_id}" and req.method == "GET":
            return httpx.Response(
                200,
                json={"id": event_id, "led_to": [anchor_id]},
            )

        return httpx.Response(404, json={})

    return MockTransport(_handler), _handler


@contextmanager
def patch_httpx(*, anchor_id: str, event_id: str):
    """
    Context-manager that monkey-patches httpx so all new AsyncClient/Client
    instances use the in-memory Memory-API stub.

    A sentinel (`httpx._memory_api_patch_active`) is toggled so tests can fail
    fast if the stub wasn’t activated.
    """
    transport, handler = _build_transport(anchor_id, event_id)

    original_async = httpx.AsyncClient          # may be a class *or* a callable factory
    original_sync  = httpx.Client

    def _wrap_async(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_async(*args, **kwargs)

    def _wrap_sync(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_sync(*args, **kwargs)

    # ───────── Build safe replacements irrespective of the original type ─────── #
    if isinstance(original_async, type):
        class _PatchedAsync(original_async):                     # type: ignore[misc]
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("transport", transport)
                super().__init__(*args, **kwargs)
    else:                                        # already monkey-patched to a function
        _PatchedAsync = _wrap_async              # type: ignore[assignment]

    if isinstance(original_sync, type):
        class _PatchedSync(original_sync):                       # type: ignore[misc]
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("transport", transport)
                super().__init__(*args, **kwargs)
    else:
        _PatchedSync = _wrap_sync                # type: ignore[assignment]

    # ──────────────── apply patch ──────────────── #
    httpx.AsyncClient = _PatchedAsync  # type: ignore[assignment]
    httpx.Client = _PatchedSync        # type: ignore[assignment]
    httpx._memory_api_handler = handler
    httpx._memory_api_patch_active = True

    try:
        yield
    finally:
        # ─────────────── restore ──────────────── #
        httpx.AsyncClient = original_async  # type: ignore[assignment]
        httpx.Client = original_sync        # type: ignore[assignment]
        httpx._memory_api_patch_active = False
