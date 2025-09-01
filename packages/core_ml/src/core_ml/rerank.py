"""
Pluggable reranker with a CPU-fast fallback.

API:
    def rerank(query: str, candidates: list[dict]) -> list[tuple[dict, float]]

If ``sentence_transformers`` with a CrossEncoder is available, use it.
Otherwise fall back to cosine similarity over embeddings served by the
``core_ml.embeddings`` client. This keeps Gateway CPU-safe while allowing
drop-in quality upgrades when GPU models are present.
"""
from __future__ import annotations
from typing import Dict, List, Tuple, Iterable
import math
import os

from core_logging import get_logger, log_stage
from core_config import get_settings
from shared.content import primary_text_and_field

logger = get_logger("core_ml.rerank")
logger.propagate = True

try:
    from sentence_transformers import CrossEncoder  # type: ignore
except Exception:  # pragma: no cover
    CrossEncoder = None  # type: ignore

_ce = None

def _load_cross_encoder():
    global _ce
    if _ce is not None:
        return _ce
    model_name = os.getenv("CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    if CrossEncoder is None:
        return None
    _ce = CrossEncoder(model_name)
    log_stage(logger, "resolver", "rerank_strategy", strategy="cross_encoder", model=model_name)
    return _ce

async def _cosine_fallback(query: str, texts: List[str]) -> List[float]:
    # CPU-fast cosine similarity using remote embeddings
    from core_ml.embeddings import embed
    vecs = await embed([query] + texts) or []
    if len(vecs) != len(texts) + 1:
        return [0.0] * len(texts)
    q = vecs[0]
    scores: List[float] = []
    for v in vecs[1:]:
        num = sum(a*b for a, b in zip(q, v))
        den = math.sqrt(sum(a*a for a in q)) * math.sqrt(sum(b*b for b in v))
        scores.append(num/den if den else 0.0)
    log_stage(logger, "resolver", "rerank_strategy", strategy="cosine_embeddings", dims=len(q))
    return scores

async def rerank(query: str, candidates: List[Dict]) -> List[Tuple[Dict, float]]:
    """
    Return ``[(candidate, score), ...]`` sorted best‑first.

    This coroutine offloads blocking CrossEncoder predictions to a background
    thread and falls back to cosine similarity using the shared embeddings client.
    A synchronous wrapper is deliberately omitted to prevent accidental event‑loop
    blocking via ``asyncio.run``.  Callers must ``await`` this function.
    """
    texts = [primary_text_and_field(c) for c in candidates]
    pairs = [(query, t) for (t, _field) in texts]
    try:
        from collections import Counter
        field_counts = Counter([f for (_t, f) in texts if f])
        log_stage(
            logger,
            "resolver",
            "rerank_pairs_built",
            field_top3=dict(field_counts.most_common(3)),
            candidate_count=len(candidates),
        )
    except Exception:
        pass
    ce = _load_cross_encoder()
    if ce is not None:
        import asyncio
        def _predict() -> Iterable[float]:
            try:
                return ce.predict(pairs)
            except Exception:
                return []
        scores = await asyncio.to_thread(_predict)
        if not scores or len(scores) != len(candidates):
            scores = await _cosine_fallback(query, [t for (t, _f) in texts])
        return sorted(zip(candidates, scores), key=lambda t: t[1], reverse=True)
    scores = await _cosine_fallback(query, [t for (t, _f) in texts])
    return sorted(zip(candidates, scores), key=lambda t: t[1], reverse=True)