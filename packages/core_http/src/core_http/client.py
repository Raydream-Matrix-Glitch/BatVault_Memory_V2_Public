import asyncio
from typing import Any, Dict, Optional

import httpx
from core_utils import jsonx

from core_config.constants import timeout_for_stage
from core_observability.otel import inject_trace_context

from core_logging import get_logger

# Module-level logger for this package
logger = get_logger("core_http")

_shared_client: httpx.AsyncClient | None = None

def _build_timeout(seconds: float) -> httpx.Timeout:
    # Separate connect/read/write/pool timeouts; read dominates
    connect = min(0.5, max(0.1, seconds * 0.3))
    read    = max(0.1, seconds)
    write   = min(seconds, 1.0)
    pool    = min(seconds, 1.0)
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)

def get_http_client(*, timeout_ms: Optional[int] = None) -> httpx.AsyncClient:
    """
    Return a process‑wide ``httpx.AsyncClient`` with sensible defaults and
    OpenTelemetry context propagation.  The returned client is shared across
    the process and **must not be closed** by callers.  If the shared client
    has been closed (for example, by legacy code calling ``aclose()`` on the
    client), a new client will be created on demand.

    Parameters
    ----------
    timeout_ms: Optional[int]
        Desired read timeout in milliseconds.  When provided and greater
        than the current client timeout, the client's timeout configuration
        will be increased; lower timeouts do not reduce the existing pool
        configuration.

    Returns
    -------
    httpx.AsyncClient
        A shared asynchronous HTTP client instance.  Closing this client is
        forbidden as it will affect all users in the process.
    """
    global _shared_client
    base_sec = (timeout_ms / 1000.0) if timeout_ms is not None else timeout_for_stage("enrich")
    # Rebuild the client if it does not yet exist or has been closed.  httpx
    # exposes an ``is_closed`` attribute that returns True after the client
    # has been closed via ``aclose()``.  In that case we discard the previous
    # instance and start afresh.
    if _shared_client is None or getattr(_shared_client, "is_closed", False):
        # If the shared client existed but was closed, emit a structured log
        # indicating that a new client is being created.
        if _shared_client is not None and getattr(_shared_client, "is_closed", False):
            try:
                logger.info(
                    "recreating_shared_client",
                    stage="http_client",
                    meta={"timeout_sec": base_sec},
                )
            except Exception:
                pass
        _shared_client = httpx.AsyncClient(timeout=_build_timeout(base_sec))
        return _shared_client
    # If a timeout is provided and exceeds the current read timeout, update
    # the client's timeout configuration.
    try:
        current_read = float(_shared_client.timeout.read)  # type: ignore[attr-defined]
        if base_sec is not None and base_sec > current_read:
            _shared_client.timeout = _build_timeout(base_sec)
    except Exception:
        pass
    return _shared_client

async def fetch_json(method: str,
                     url: str,
                     *,
                     json: Any | None = None,
                     headers: Optional[Dict[str, str]] = None,
                     retry: int = 0,
                     stage: str = "enrich",
                     request_id: str | None = None) -> Any:
    """
    Minimal JSON fetch with OTEL header injection and bounded retry.
    Raises on non-2xx.
    """
    client = get_http_client(timeout_ms=int(timeout_for_stage(stage) * 1000))
    hdrs = inject_trace_context(headers or {})
    last_exc: Exception | None = None
    for attempt in range(max(0, int(retry)) + 1):
        try:
            resp = await client.request(method.upper(), url, json=json, headers=hdrs)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(f"{resp.status_code} on {url}", request=resp.request, response=resp)
            try:
                return resp.json()
            except Exception:
                # Parse via the repo's canonical JSON loader to remain
                # consistent with auditing/replay and avoid silent drift.
                return jsonx.loads(resp.content)
        except Exception as e:
            last_exc = e
            if attempt < max(0, int(retry)):
                await asyncio.sleep(0.05 + 0.2 * (attempt % 3))
                continue
            break
    assert last_exc is not None
    raise last_exc