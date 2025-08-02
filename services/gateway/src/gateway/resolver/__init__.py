from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

import httpx
import orjson
import redis.asyncio as redis
import core_metrics

from .embedding_model import encode
from .reranker import rerank
from .fallback_search import search_bm25
from core_logging import trace_span
from core_config import get_settings

settings = get_settings()
CACHE_TTL = 300  # seconds

# ---------------------------------------------------------------------------#
# Redis connection (optional – falls back to None when Redis is unavailable) #
# ---------------------------------------------------------------------------#
try:
    _redis: redis.Redis | None = redis.from_url(settings.redis_url)
except Exception:          # pragma: no-cover  – local pytest w/o real Redis
    _redis = None

# ---------------------------------------------------------------------------#
# Pre-compiled slug regex (spec §B-2) – slug fast-path to skip BM25/X-enc.   #
# ---------------------------------------------------------------------------#
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")

@trace_span("resolve")
async def resolve_decision_text(text: str) -> Dict[str, Any] | None:
    """
    Resolve *text* (decision slug **or** NL query) to a Decision anchor.
    Fast-path: if *text* already **looks like** a slug → hit Memory-API
               `GET /api/enrich/decision/{id}` directly
    Slow-path: otherwise run BM25 → cross-encoder rerank pipeline.
    """

    # ---------- 1️⃣  slug short-circuit ---------------------------------- #
    if _SLUG_RE.match(text):
        try:
            async with httpx.AsyncClient(timeout=0.25) as client:         # ≤ 800 ms spec budget
                resp = await client.get(f"{settings.memory_api_url}/api/enrich/decision/{text}")
            if resp.status_code == 200:
                core_metrics.counter("resolver_slug_short_circuit_total", 1)
                return resp.json()
        except Exception:
            core_metrics.counter("resolver_slug_short_circuit_error_total", 1)
# ---------- 2️⃣  BM25 → Cross-encoder path ------------------------------- #
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