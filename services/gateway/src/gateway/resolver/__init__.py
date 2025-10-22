from __future__ import annotations
import hashlib
import os
import re
import inspect
from typing import Dict, Any
from redis.exceptions import RedisError, ConnectionError
from core_http.client import fetch_json
from core_utils import jsonx
from core_cache.redis_client import get_redis_pool
from core_metrics import counter as _metric_counter, histogram as _metric_histogram
from core_logging import trace_span, get_logger, log_stage
from .fallback_search import search_bm25
from core_utils.domain import is_valid_anchor
from core_config import get_settings
from core_config.constants import TTL_EVIDENCE_CACHE_SEC as CACHE_TTL

settings = get_settings()
logger = get_logger("gateway.resolver")

# ---------------------------------------------------------------------------
# Dynamic configuration
#
# The BM25 resolver limits the number of candidate documents to a fixed
# constant.  Historically this value was hard‑coded to 24.  Operators may
# wish to tune this limit depending on the size of the index or latency
# requirements.  The ``RESOLVER_BM25_TOPK`` environment variable allows for
# such tuning.  Non‑integer or missing values will fall back to 24.
try:
    _BM25_TOPK = int(os.getenv("RESOLVER_BM25_TOPK", "24"))
except (ValueError, TypeError, OverflowError):
    _BM25_TOPK = 24

# ---------------------------------------------------------------------------#
# Redis connection (optional – falls back to None when Redis is unavailable) #
# ---------------------------------------------------------------------------#
try:
    # Use the shared Redis pool for all resolver operations
    _redis = get_redis_pool()
except (RedisError, ConnectionError, OSError, ImportError, AttributeError, RuntimeError):  # pragma: no-cover (local pytest)
    _redis = None

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

async def _cache_ttl(key: str) -> int | None:
    """Return TTL seconds when available; None on failure."""
    if _redis is None:
        return None
    try:
        res = _redis.ttl(key)
        return await res if inspect.isawaitable(res) else res
    except (RedisError, ConnectionError, OSError):
        _metric_counter("cache_error_total", 1, service="resolver")
        return None

_NEG_SENTINEL = jsonx.dumps({"_neg": True}).encode("utf-8")

async def _schedule_refresh(key: str, text: str, request_id: str | None, snapshot_etag: str | None):
    """Fire-and-forget refresh to repopulate a near-expiry entry."""
    async def _refresher():
        try:
            best = await resolve_decision_text(text, request_id=request_id, snapshot_etag=snapshot_etag)
            # Only write if we actually got something (avoid overwriting a positive with NEG)
            if best:
                await _cache_setex(key, CACHE_TTL, jsonx.dumps(best))
                log_stage(logger, "resolver", "swr_refresh_ok",
                          cache_key=key, request_id=request_id, snapshot_etag=snapshot_etag)
        except (RedisError, ConnectionError, OSError, RuntimeError, ValueError, TypeError) as _e:  # pragma: no cover — never raise in background
            log_stage(logger, "resolver", "swr_refresh_skip",
                      reason=type(_e).__name__, cache_key=key, request_id=request_id, snapshot_etag=snapshot_etag)
    try:
        import asyncio
        asyncio.create_task(_refresher())
    except (RuntimeError, ImportError):
        pass

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
@trace_span("resolver", logger=logger)
async def resolve_decision_text(
    text: str,
    *,
    request_id: str | None = None,
    snapshot_etag: str | None = None,
) -> Dict[str, Any] | None:
    """
    Resolve *text* (anchor **or** NL query) to an anchor.

    Reliability rules (Milestone-3+):
    • Redis or Memory-API outages must degrade gracefully.
    • Function must never raise; on failure it returns *None*.
    """

    # ---------- 1️⃣  Anchor short-circuit -------------------------------- #
    _text = (text or "").strip()
    if is_valid_anchor(_text):
        cache_key = f"resolver:{text}"
        cached = await _cache_get(cache_key)
        if cached:
            _metric_counter("cache_hit_total", 1, service="resolver")
            return jsonx.loads(cached)

        try:
            log_stage(
                logger, "resolver", "anchor_short_circuit_start",
                anchor=_text,
                decision_ref=_text,
                request_id=request_id,
                snapshot_etag=snapshot_etag,
            )
            data = await fetch_json(
                "GET",
                f"{settings.memory_api_url}/api/enrich",
                params={"anchor": _text},
                stage="enrich",
            )
            if isinstance(data, dict) and data.get("id") == _text:
                _metric_counter("resolver_anchor_short_circuit_total", 1, service="resolver")
                await _cache_setex(cache_key, CACHE_TTL, jsonx.dumps(data))
                log_stage(
                    logger,
                    "resolver", "anchor_short_circuit_end",
                    cache_key=cache_key,
                    ok=True,
                    request_id=request_id,
                    snapshot_etag=snapshot_etag,
                )
                return data
        except (OSError, RuntimeError, ValueError, TypeError):
            # Record failures without raising; the resolver must degrade gracefully.
            _metric_counter("resolver_anchor_short_circuit_error_total", 1, service="resolver")

    # ---------- 2️⃣  BM25 → Cross-encoder path --------------------------- #
    key = "resolver:" + hashlib.sha256(text.encode()).hexdigest()
    cached = await _cache_get(key)
    if cached:
        _metric_counter("cache_hit_total", 1, service="resolver")
        try:
            # Negative sentinel fast-path
            if bytes(cached) == _NEG_SENTINEL:
                log_stage(logger, "resolver", "neg_cache_hit",
                          cache_key=key, request_id=request_id, snapshot_etag=snapshot_etag)
                return None
            # SWR: if TTL is low, issue background refresh
            ttl = await _cache_ttl(key)
            if isinstance(ttl, int) and ttl >= 0 and ttl < max(1, int(CACHE_TTL * 0.2)):
                log_stage(logger, "resolver", "swr_refresh_scheduled",
                          cache_key=key, ttl=ttl, request_id=request_id, snapshot_etag=snapshot_etag)
                await _schedule_refresh(key, text, request_id, snapshot_etag)
        except (RuntimeError, ValueError, TypeError):
            pass
        return jsonx.loads(cached)

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
        except (AttributeError, ImportError):
            search_fn = search_bm25
        candidates = await search_fn(
            text,
            k=_BM25_TOPK,
            request_id=request_id,
            snapshot_etag=snapshot_etag,
        )
    except Exception:                                # network / timeout
        _metric_counter("bm25_search_error_total", 1, service="resolver")
        candidates = []

    if not candidates:
        # Negative cache to prevent thundering herds on repeated no-hit text
        await _cache_setex(key, CACHE_TTL, _NEG_SENTINEL)
        log_stage(logger, "resolver", "neg_cache_store", cache_key=key)
        return None

    # Legacy best-only path kept for compatibility with old callers.
    # Prefer using `search_candidates(...)` below in new code.
    best = candidates[0]
    try:
        log_stage(logger, "resolver", "rerank_bypassed",
                  reason="ml_disabled", request_id=request_id, snapshot_etag=snapshot_etag)
    except (RuntimeError, ValueError, TypeError, KeyError):
        pass

    await _cache_setex(key, CACHE_TTL, jsonx.dumps(best))
    return best

async def search_candidates(
    q: str,
    k: int = 24,
    *,
    request_id: str | None = None,
    snapshot_etag: str | None = None,
    policy_headers: dict[str, str] | None = None,
) -> list[dict]:
    """
    Deterministic candidate resolution with optional semantic rerank:
      1) BM25 (Memory /api/resolve/text) → top-K candidates
      2) If RERANK_ENABLE: try to import `gateway.resolver.reranker` and rerank
      3) Return a stable, descending score order; ties broken by id asc
    """
    candidates = await search_bm25(
        q, k=k, request_id=request_id, snapshot_etag=snapshot_etag, policy_headers=policy_headers
    )
    if not candidates:
        return []

    if settings.rerank_enable and 2 <= len(candidates) <= int(getattr(settings, "rerank_pair_max", 10)):
        try:
            from .reranker import rerank as _rerank  # type: ignore
        except ImportError:
            log_stage(logger, "resolver", "rerank_bypassed",
                      reason="module_missing", request_id=request_id, snapshot_etag=snapshot_etag)
        else:
            pairs = await _rerank(q, candidates)
            candidates = [{**cand, "score": float(score)} for (cand, score) in pairs]

    # Stable, deterministic ordering: (-score, id)
    def _score(x: dict) -> float:
        try:
            return float(x.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    candidates.sort(key=lambda c: (-_score(c), str(c.get("id") or "")))
    return candidates
