from __future__ import annotations
import asyncio
import random
from typing import Any, Dict, Optional

import httpx
import orjson

from core_logging import get_logger
from .logging_helpers import stage as log_stage
from core_observability.otel import inject_trace_context
from core_config import get_settings
from core_config.constants import timeout_for_stage

_logger = get_logger("gateway.http")

_client: httpx.AsyncClient | None = None

def get_http_client(timeout_ms: Optional[int] = None) -> httpx.AsyncClient:
    """
    Return a shared Async HTTP client pre-configured with OTEL headers and sane timeouts.
    The client is reused across requests to avoid connection churn. Timeout defaults
    to stage-based values via `timeout_for_stage("enrich")` unless explicitly provided.
    """
    global _client
    # Compute separate connect/read/write timeouts. Read dominates; connect is capped.
    base_sec = float(timeout_ms) / 1000.0 if timeout_ms is not None else timeout_for_stage("enrich")
    connect_sec = min(0.4, max(0.1, base_sec * 0.4))
    read_sec    = base_sec
    write_sec   = max(0.2, min(base_sec, 1.0))
    pool_sec    = max(0.2, min(base_sec, 1.0))
    to = httpx.Timeout(connect=connect_sec, read=read_sec, write=write_sec, pool=pool_sec)
    limits = httpx.Limits(max_keepalive_connections=32, max_connections=128)
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=to, limits=limits)
    else:
        # Update timeouts dynamically for the shared client
        _client.timeout = to
    return _client

async def fetch_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Dict[str, Any]] = None,
    stage: str = "enrich",
    retry: int = 1,
    timeout_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Unified JSON HTTP helper with OTEL propagation, jittered single retry and
    stage-aware timeouts. Returns decoded JSON; if upstream is non-JSON,
    return a dict with `_status` and `raw`. Never caches errors.
    """
    client = get_http_client(timeout_ms or int(timeout_for_stage(stage) * 1000))
    req_headers = {"user-agent": "batvault-gateway/1"}
    if headers:
        req_headers.update(headers)
    req_headers = inject_trace_context(req_headers)
    last_exc: Exception | None = None

    for attempt in range(0, max(0, int(retry)) + 1):
        try:
            if method.upper() == "GET":
                resp = await client.get(url, headers=req_headers)
            elif method.upper() == "POST":
                resp = await client.post(url, headers=req_headers, json=json)
            else:
                raise ValueError(f"Unsupported method: {method}")

            status = int(resp.status_code)
            try:
                data = resp.json()
            except Exception:
                data = {"raw": await resp.aread()}

            if isinstance(data, dict):
                data.setdefault("_status", status)
                et = resp.headers.get("x-snapshot-etag") or resp.headers.get("etag") or ""
                if et:
                    data.setdefault("snapshot_etag", et)

            if status >= 400:
                try:
                    log_stage("http", "error", url=url, status=status, body_len=len(orjson.dumps(data)))
                except Exception:
                    pass
                resp.raise_for_status()
            return data
        except Exception as e:
            last_exc = e
            if attempt < max(0, int(retry)):
                await asyncio.sleep(random.uniform(0.05, 0.3))
                continue
            break

    assert last_exc is not None
    raise last_exc