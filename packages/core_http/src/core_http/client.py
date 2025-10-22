import asyncio, time
from typing import Any, Dict, Optional
import httpx
from core_utils import jsonx
from core_config.constants import timeout_for_stage, HTTP_RETRY_BASE_MS, HTTP_RETRY_JITTER_MS
from core_observability.otel import inject_trace_context
from core_logging import get_logger, log_stage
from core_logging import current_request_id 
from urllib.parse import urlsplit
from core_utils.backoff import compute_backoff_delay_ms, async_backoff_sleep

# Module-level logger for this package
logger = get_logger("core_http")

_shared_client: httpx.AsyncClient | None = None
_shared_client_sync: httpx.Client | None = None

def _inject_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Merge caller headers with process context (trace + request-id).
    Never mutates the input dict.
    """
    base: Dict[str, str] = {}
    try:
        base = inject_trace_context({}) or {}
    except (RuntimeError, ValueError):
        base = {}
    # Propagate request id if bound (root/ingress responsibility)
    rid = current_request_id()
    if rid:
        base.setdefault("x-request-id", rid)
    if headers:
        base.update(headers)
    return base

def _jsonable(x: Any) -> Any:
    """
    Recursively coerce payloads so they are JSON-serializable.
    - bytes/bytearray → UTF-8 (replace) or hex fallback
    - dict/list/tuple → walk recursively
    """
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode("utf-8", "replace")
        except UnicodeDecodeError:
            return x.hex()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x

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
            log_stage(
                logger, "http.client", "recreating_shared_client",
                timeout_sec=base_sec, request_id=(current_request_id() or "startup")
            )
        _shared_client = httpx.AsyncClient(timeout=_build_timeout(base_sec))
        return _shared_client
    # If a timeout is provided and exceeds the current read timeout, update
    # the client's timeout configuration.
    current_read = float(_shared_client.timeout.read)  # type: ignore[attr-defined]
    if base_sec is not None and base_sec > current_read:
        _shared_client.timeout = _build_timeout(base_sec)
    return _shared_client

async def fetch_json(method: str,
                     url: str,
                     *,
                     json: Any | None = None,
                     params: Optional[Dict[str, Any]] = None,
                     headers: Optional[Dict[str, str]] = None,
                     retry: int = 0,
                     stage: str = "enrich",
                     request_id: str | None = None,
                     return_headers: bool = False) -> Any:
    """
    Minimal JSON fetch with OTEL header injection and bounded retry.
    Raises on non-2xx.

    When ``return_headers`` is True, returns a tuple ``(data, headers)`` where
    ``headers`` is a plain ``dict`` of response headers.
    JSON payloads and header values are normalized to be JSON-serializable.
    """
    client = get_http_client(timeout_ms=int(timeout_for_stage(stage) * 1000))
    hdrs = _inject_headers(headers or {})
    parts = urlsplit(url)
    # Strategic structured logging: request breadcrumb (no params)
    log_stage(
        logger, "http.client", "http.client.request",
        request_id=request_id or current_request_id(),
        op=f"{method.upper()} {(parts.hostname or '')}{parts.path or '/'}",
        http={
            "method": method.upper(),
            "scheme": parts.scheme or "http",
            "host": parts.hostname or "",
            "target": parts.path or "/",
        },
        param_keys=sorted(list((params or {}).keys()))
    )
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(max(0, int(retry)) + 1):
        try:
            resp = await client.request(method.upper(), url, json=json, params=params, headers=hdrs)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(f"{resp.status_code} on {url}", request=resp.request, response=resp)
            # Response breadcrumb with normalized http + duration
            dt_ms = (time.perf_counter() - t0) * 1000.0
            log_stage(
                logger, "http.client", "http.client.response",
                request_id=request_id or current_request_id(),
                op=f"{method.upper()} {(parts.hostname or '')}{parts.path or '/'}",
                http={
                    "method": method.upper(),
                    "scheme": parts.scheme or "http",
                    "host": parts.hostname or "",
                    "target": parts.path or "/",
                    "status_code": resp.status_code,
                },
                latency_ms=int(dt_ms),
            )
            try:
                data = resp.json()
            except ValueError:
                # Parse via the repo's canonical JSON loader to remain
                # consistent with auditing/replay and avoid silent drift.
                data = jsonx.loads(resp.content)
            # Ensure downstream JSONResponse can serialize deterministically
            data = _jsonable(data)
            if return_headers:
                # httpx headers are case-insensitive; convert to a plain dict
                # to avoid leaking library types across package boundaries.
                hdrs = {str(k): str(v) for k, v in dict(resp.headers).items()}
                return data, hdrs
            return data
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < max(0, int(retry)):
                delay_ms = compute_backoff_delay_ms(
                    attempt + 1,
                    base_ms=HTTP_RETRY_BASE_MS,
                    jitter_ms=HTTP_RETRY_JITTER_MS,
                    mode="decorrelated",
                )
                log_stage(
                    logger, "http.client", "http.client.retry_sleep",
                    request_id=request_id or current_request_id(),
                    attempt=attempt + 1,
                    delay_ms=delay_ms,
                    url=url,
                )
                await async_backoff_sleep(attempt + 1, base_ms=HTTP_RETRY_BASE_MS, jitter_ms=HTTP_RETRY_JITTER_MS, mode="decorrelated")
                continue
            break
        # Let non-HTTP errors (e.g., JSON handling bugs) surface immediately.
        except Exception:
            raise
    assert last_exc is not None
    raise last_exc

# ────────────────────────────────────────────────────────────
# Sync client (for core_storage & bootstrap paths)
# ────────────────────────────────────────────────────────────
def get_sync_http_client(timeout_ms: Optional[int] = None) -> httpx.Client:
    """
    Return a shared sync HTTP client with sane timeouts and HTTP/1.1 keep-alive.
    """
    global _shared_client_sync
    base_sec = (timeout_ms / 1000.0) if timeout_ms is not None else timeout_for_stage("enrich")
    if _shared_client_sync is None or _shared_client_sync.is_closed:
        _shared_client_sync = httpx.Client(timeout=httpx.Timeout(connect=base_sec, read=base_sec, write=base_sec))
    return _shared_client_sync

def fetch_json_sync(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout_ms: Optional[int] = None,
    retry: int = 2,
    return_headers: bool = False,
) -> Any:
    """
    Sync twin of fetch_json. Applies retry/jitter and injects trace + request-id.
    """
    client = get_sync_http_client(timeout_ms=timeout_ms)
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(retry) + 1)):
        try:
            resp = client.request(method.upper(), url, headers=_inject_headers(headers), json=json, params=params)
            # Raise for HTTP errors >= 400 to trigger retry policy (except 4xx on last attempt)
            if resp.status_code >= 400 and attempt < int(retry):
                raise httpx.HTTPStatusError(f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp)
            # As with async, parse deterministically
            data = jsonx.loads(resp.content) if resp.content else None
            data = _jsonable(data)
            if return_headers:
                hdrs = {str(k): str(v) for k, v in dict(resp.headers).items()}
                return data, hdrs
            return data
        except httpx.HTTPError as e:
            last_exc = e
            if attempt < int(retry):
                delay_ms = compute_backoff_delay_ms(
                    attempt + 1,
                    base_ms=HTTP_RETRY_BASE_MS,
                    jitter_ms=HTTP_RETRY_JITTER_MS,
                    mode="decorrelated",
                )
                try:
                    log_stage(
                        logger, "http.client", "http.client.retry_sleep",
                        request_id=current_request_id(),
                        attempt=attempt + 1,
                        delay_ms=delay_ms,
                        url=url,
                    )
                except Exception:
                    # Logging must never break the retry path
                    pass
                time.sleep(delay_ms / 1000.0)
                continue
            break
        except Exception:
            # Non-HTTP exceptions are not retried to avoid masking bugs.
            raise
    assert last_exc is not None
    raise last_exc