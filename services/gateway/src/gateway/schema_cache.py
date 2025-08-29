from __future__ import annotations
import time
from typing import Dict, Tuple

from core_config import get_settings
from core_config.constants import TTL_SCHEMA_CACHE_SEC
from .http import fetch_json

_CACHE: Dict[str, Tuple[dict, str, float]] = {}

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