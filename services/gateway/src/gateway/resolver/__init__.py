from __future__ import annotations

import hashlib
from typing import Any, Dict, List

import orjson
import redis.asyncio as redis
import core_metrics

from .embedding_model import encode
from .reranker import rerank
from .fallback_search import search_bm25
from core_logging import trace_span
from core_config import get_settings

settings = get_settings()
_redis = redis.from_url(settings.redis_url, decode_responses=False)

CACHE_TTL = 300  # seconds


@trace_span("resolve")
async def resolve_decision_text(text: str) -> Dict[str, Any] | None:
    key = "resolver:" + hashlib.sha256(text.encode()).hexdigest()
    if _redis:
        cached = await _redis.get(key)
        if cached:
            core_metrics.counter("cache_hit_total", 1, service="resolver")
            return orjson.loads(cached)
    # cache miss
    core_metrics.counter("cache_miss_total", 1, service="resolver")

    candidates = await search_bm25(text, k=24)
    if not candidates:
        return None

    ranked = rerank(text, candidates)
    best_candidate, best_score = ranked[0]
    core_metrics.histogram("resolver_confidence", float(best_score))
    best = best_candidate

    if _redis:
        await _redis.setex(key, CACHE_TTL, orjson.dumps(best))
    return best