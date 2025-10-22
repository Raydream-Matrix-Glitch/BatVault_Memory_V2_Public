from __future__ import annotations
import time
from typing import Any, Dict, Tuple, Optional

from core_config import get_settings
from core_config.constants import TTL_SCHEMA_CACHE_SEC
from core_http.client import fetch_json

# Versioned cache entries keyed by 'schema:{path}:{etag}' or 'policy:{etag}'.
# Values: (data, etag, expires_at_epoch_seconds)
_CACHE: Dict[str, Tuple[dict, str, float]] = {}
# Latest pointers keyed by canonical path (e.g., '/api/schema/fields' or '/api/policy/registry')
# Values: (versioned_key, expires_at_epoch_seconds)
_LATEST: Dict[str, Tuple[str, float]] = {}

def _as_str(x: Any) -> str:
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode("utf-8", "replace")
        except Exception:
            return x.hex()
    return str(x)

def _extract_etag(headers: Optional[dict], body: Optional[dict]) -> str:
    """Best-effort extraction of a version marker.

    Preference order:
    1) HTTP headers: 'ETag' (case-insensitive), then 'x-snapshot-etag'/'snapshot_etag'
    2) Body fields: meta.snapshot_etag, snapshot_etag, etag
    Returns empty string if nothing is found.
    """
    if headers:
        try:
            items = dict(headers).items()
        except Exception:
            items = []
        lower = {str(k).lower(): _as_str(v) for k, v in items}
        if lower.get("etag"):
            return lower["etag"]
        for k in ("x-snapshot-etag", "snapshot_etag"):
            if lower.get(k):
                return lower[k]
    if isinstance(body, dict):
        meta = body.get("meta") or {}
        if meta.get("snapshot_etag"):
            return _as_str(meta["snapshot_etag"])
        if body.get("snapshot_etag"):
            return _as_str(body["snapshot_etag"])
        if body.get("etag"):
            return _as_str(body["etag"])
    return ""

def get_cached(path: str) -> Tuple[Optional[dict], str]:
    """
    Return a cached document (data, etag) if present and not expired.
    Never performs I/O. If missing or expired, returns (None, "").
    Versioned cache under the hood; resolves the latest version for the canonical path.
    """
    now = time.time()
    latest = _LATEST.get(path)
    if not latest:
        return None, ""
    version_key, expires_at = latest
    if expires_at <= now:
        _LATEST.pop(path, None)
        return None, ""
    entry = _CACHE.get(version_key)
    if not entry:
        _LATEST.pop(path, None)
        return None, ""
    data, etag, _ = entry
    return data, etag

async def fetch_schema(path: str) -> Tuple[dict, str]:
    """
    Fetch a schema/helper document from Memory API and cache it using a *versioned* key.

    Args:
        path: Canonical API path, e.g. "/api/schema/fields" or "/api/schema/rels".

    Returns:
        (data, etag) where etag is best-effort (may be empty if the upstream
        endpoint does not include an etag/snapshot marker in the body).
    """
    # Serve from cache when valid
    cached, etag = get_cached(path)
    if cached is not None:
        return cached, etag

    s = get_settings()
    base = (getattr(s, "memory_api_url", "") or "").rstrip("/")
    url = f"{base}{path}"

    # Fetch headers so we can key by version marker
    data, headers = await fetch_json("GET", url, stage="schema", return_headers=True)
    etag = _extract_etag(headers, data) or ""
    version_key = f"schema:{path}:{etag or '_ttl'}"

    expires_at = time.time() + TTL_SCHEMA_CACHE_SEC
    _CACHE[version_key] = (data, etag, expires_at)
    _LATEST[path] = (version_key, expires_at)
    return data, etag

async def fetch_policy_registry() -> Tuple[Optional[dict], str]:
    """
    Fetch and cache the policy registry using a *versioned* key (policy:{etag}).
    Returns (data, etag). If URL not configured, returns (None, "").
    """
    s = get_settings()
    url = (getattr(s, "policy_registry_url", None) or "").strip()
    if not url:
        return None, ""
    data, headers = await fetch_json("GET", url, stage="schema", return_headers=True)
    etag = _extract_etag(headers, data) or ""
    version_key = f"policy:{etag or '_ttl'}"
    expires_at = time.time() + TTL_SCHEMA_CACHE_SEC
    _CACHE[version_key] = (data, etag, expires_at)
    _LATEST["/api/policy/registry"] = (version_key, expires_at)
    return data, etag