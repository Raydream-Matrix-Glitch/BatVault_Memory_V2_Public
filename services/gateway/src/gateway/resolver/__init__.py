from __future__ import annotations

import hashlib
import re
import inspect
from typing import Dict, Any
from redis.exceptions import RedisError, ConnectionError

from gateway.http import fetch_json
import orjson
from gateway.redis import get_redis_pool
from ..metrics import counter as _metric_counter, histogram as _metric_histogram

from core_logging import trace_span, get_logger
from .logging_helpers import stage as log_stage
from .reranker import rerank
from .fallback_search import search_bm25
from core_utils import is_slug
from core_config import get_settings
from core_config.constants import TTL_RESOLVER_CACHE_SEC as CACHE_TTL

settings = get_settings()


# ---------------------------------------------------------------------------#
# Redis connection (optional – falls back to None when Redis is unavailable) #
# ---------------------------------------------------------------------------#
try:
    # Use the shared Redis pool for all resolver operations
    _redis = get_redis_pool()
except Exception:                       # pragma: no-cover (local pytest)
    _redis = None

logger = get_logger("gateway.resolver")

# ---------------------------------------------------------------------------#
# Cache helpers – swallow Redis errors so tests run w/o a live server        #
# ---------------------------------------------------------------------------#
async def _cache_get(key: str):
    """Best-effort GET; never raises when Redis is unhealthy."""
    if _redis is None:
        return None
    try:
        result = _redis.get(key)
        return await result if inspect.isawaitable(result) else result
    except (RedisError, ConnectionError, OSError):
        _metric_counter("cache_error_total", 1, service="resolver")
        return None


async def _cache_setex(key: str, ttl: int, value: bytes):
    """Best-effort SETEX; silently ignored on connection problems."""
    if _redis is None:
        return
    try:
        # Support both async and sync Redis clients / test doubles (M3→M4 resiliency)
        result = _redis.setex(key, ttl, value)
        if inspect.isawaitable(result):
            await result
        # else: best-effort synchronous client; nothing to await
    except (RedisError, ConnectionError, OSError, TypeError, AttributeError):
        _metric_counter("cache_error_total", 1, service="resolver")

# ---------------------------------------------------------------------------#
# Public API                                                                 #
# ---------------------------------------------------------------------------#
@trace_span("resolve")
async def resolve_decision_text(text: str) -> Dict[str, Any] | None:
    """
    Resolve *text* (decision slug **or** NL query) to a Decision anchor.

    Reliability rules (Milestone-3+):
    • Redis or Memory-API outages must degrade gracefully.
    • Function must never raise; on failure it returns *None*.
    """

    # ---------- 1️⃣  Slug short-circuit ---------------------------------- #
    _text = (text or "").strip()
    if is_slug(_text):
        cache_key = f"resolver:{text}"
        cached = await _cache_get(cache_key)
        if cached:
            _metric_counter("cache_hit_total", 1, service="resolver")
            return orjson.loads(cached)

        try:
            # Start the slug short‑circuit via the unified fetch_json helper.  This will
            # propagate the current trace context automatically and apply stage‑based
            # timeouts and retries.  If the enrich call succeeds and returns a
            # matching ID, update the cache and metrics accordingly.
            log_stage("resolver", "slug_short_circuit_start", decision_ref=_text)
            data = await fetch_json(
                "GET",
                f"{settings.memory_api_url}/api/enrich/decision/{_text}",
                stage="enrich",
            )
            if isinstance(data, dict) and data.get("id") == _text:
                _metric_counter("resolver_slug_short_circuit_total", 1, service="resolver")
                await _cache_setex(cache_key, CACHE_TTL, orjson.dumps(data))
                log_stage("resolver", "slug_short_circuit_end", cache_key=cache_key, ok=True)
                return data
        except Exception:
            # Record failures without raising; the resolver must degrade gracefully.
            _metric_counter("resolver_slug_short_circuit_error_total", 1, service="resolver")

    # ---------- 2️⃣  BM25 → Cross-encoder path --------------------------- #
    key = "resolver:" + hashlib.sha256(text.encode()).hexdigest()
    cached = await _cache_get(key)
    if cached:
        _metric_counter("cache_hit_total", 1, service="resolver")
        return orjson.loads(cached)

    _metric_counter("cache_miss_total", 1, service="resolver")

    # BM25 search with graceful degradation.  Dynamically resolve the
    # search function at runtime so that monkey‑patches on
    # ``gateway.resolver.fallback_search.search_bm25`` take effect.
    try:
        import importlib, sys
        try:
            mod = sys.modules.get("gateway.resolver.fallback_search")
            if mod is None:
                mod = importlib.import_module("gateway.resolver.fallback_search")
            search_fn = getattr(mod, "search_bm25")
        except Exception:
            search_fn = search_bm25
        candidates = await search_fn(text, k=24)
    except Exception:                                # network / timeout
        _metric_counter("bm25_search_error_total", 1, service="resolver")
        candidates = []

    if not candidates:
        return None

    ranked = rerank(text, candidates)
    best_candidate, best_score = ranked[0]
    _metric_histogram("resolver_confidence", float(best_score))
    best = best_candidate

    await _cache_setex(key, CACHE_TTL, orjson.dumps(best))
    return best
