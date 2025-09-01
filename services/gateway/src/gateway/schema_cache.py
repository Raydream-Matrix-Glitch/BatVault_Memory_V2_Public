from __future__ import annotations
import time
from typing import Dict, Tuple, Optional

from core_config import get_settings
from core_config.constants import TTL_SCHEMA_CACHE_SEC
from .http import fetch_json

_CACHE: Dict[str, Tuple[dict, str, float]] = {}

def get_cached(path: str) -> Tuple[Optional[dict], str]:
    """
    Return a cached schema (data, etag) if present and not expired.
    Never performs I/O. If missing or expired, returns (None, "").
    """
    now = time.time()
    key = path
    entry = _CACHE.get(key)
    if not entry:
        return None, ""
    data, etag, expires_at = entry
    if expires_at <= now:
        _CACHE.pop(key, None)
        return None, ""
    return data, etag

async def fetch_schema(path: str) -> Tuple[dict, str]:
    """
    Read-through in-memory cache for Memory API schema mirror.
    Returns (json, etag). Uses unified HTTP client and a single TTL.
    """
    now = time.time()
    key = path
    cached = _CACHE.get(key)
    if cached and cached[2] > now:
        return cached[0], cached[1]

    s = get_settings()
    base = s.memory_api_url.rstrip("/")
    url = f"{base}{path}"
    try:
        data = await fetch_json("GET", url, stage="schema")
    except Exception:
        # Do not cache errors
        raise

    etag = ""
    if isinstance(data, dict):
        etag = str(data.get("snapshot_etag") or "")

    _CACHE[key] = (data, etag, now + TTL_SCHEMA_CACHE_SEC)
    return data, etag

async def fetch_policy_registry() -> Tuple[Optional[dict], str]:
    """Warm the policy registry cache from a configured absolute URL.

    If POLICY_REGISTRY_URL is unset, this is a no-op that returns (None, "").
    On success, the fetched data is cached under the canonical key
    "/api/policy/registry" so that get_cached() callers can reuse it
    without issuing network I/O.
    """
    s = get_settings()
    url = (getattr(s, "policy_registry_url", None) or "").strip()
    if not url:
        # Not configured; nothing to do.
        return None, ""
    data = await fetch_json("GET", url, stage="schema")
    # Best-effort: extract an ETag-like value if present
    etag = ""
    if isinstance(data, dict):
        etag = str(data.get("snapshot_etag") or data.get("etag") or "")
    # Cache under canonical key
    key = "/api/policy/registry"
    _CACHE[key] = (data, etag, time.time() + TTL_SCHEMA_CACHE_SEC)
    return data, etag