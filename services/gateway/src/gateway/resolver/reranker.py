from __future__ import annotations
import asyncio, hashlib, inspect
from typing import List, Tuple
from core_config import get_settings
from core_cache.redis_client import get_redis_pool
from core_utils import jsonx
from core_logging import get_logger, log_stage, current_request_id

logger = get_logger("gateway.resolver.reranker")

_S = get_settings()

def _pick_text(c: dict) -> str:
    # Prefer short, human text; fall back deterministically
    return str(
        c.get("snippet")
        or c.get("text")
        or c.get("title")
        or c.get("name")
        or c.get("content")
        or ""
    )

def _score_sync(model_name: str, query: str, texts: list[str]) -> list[float]:
    # Imported lazily so the feature is truly optional.
    from transformers import AutoTokenizer, AutoModelForSequenceClassification  # type: ignore
    import torch  # type: ignore
    tok = AutoTokenizer.from_pretrained(model_name)
    mdl = AutoModelForSequenceClassification.from_pretrained(model_name)
    inputs = tok([query] * len(texts), texts, truncation=True, padding=True, max_length=384, return_tensors="pt")
    with torch.no_grad():
        out = mdl(**inputs)
        logits = out.logits
    # Support both regression (1-dim) and classification heads
    if logits.dim() == 2 and logits.size(1) == 1:
        return logits.squeeze(1).tolist()
    return logits.max(dim=1).values.tolist()

async def rerank(query: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """
    Return [(candidate, score)] in descending score order.
    Only re-scores top-N (RERANK_PAIR_MAX) candidates.
    """
    n = len(candidates)
    if n < 2:
        return [(c, float(c.get("score") or 0.0)) for c in candidates]

    pair_max = int(getattr(_S, "rerank_pair_max", 10))
    items = candidates[:pair_max]
    texts = [_pick_text(c) for c in items]

    # Cache key: model + query + ordered ids (shortened hash)
    ids = ",".join(str(c.get("id") or "") for c in items)
    h = hashlib.sha256((query + "|" + ids).encode("utf-8")).hexdigest()[:24]
    key = f"rr:cx:v1:{h}"
    rc = get_redis_pool()
    if rc is not None:
        cached = rc.get(key)
        cached = await cached if inspect.isawaitable(cached) else cached
        if cached:
            try:
                arr = [float(x) for x in jsonx.loads(cached)]
            except (ValueError, TypeError):
                arr = []
            if arr:
                log_stage(
                    logger, "resolver", "rerank_cache_hit",
                    count=len(arr), request_id=(current_request_id() or "unknown")
                )
                pairs = list(zip(items, arr[: len(items)]))
                pairs.sort(key=lambda t: (-t[1], str(t[0].get("id") or "")))
                return pairs

    model_name = getattr(_S, "cross_encoder_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    timeout = float(getattr(_S, "rerank_timeout_ms", 50)) / 1000.0
    loop = asyncio.get_running_loop()
    try:
        scores = await asyncio.wait_for(
            loop.run_in_executor(None, _score_sync, model_name, query, texts),
            timeout,
        )
    except asyncio.TimeoutError:
        # Budget exceeded → keep BM25 ordering/scores
        return [(c, float(c.get("score") or 0.0)) for c in items]
    except ImportError:
        # transformers/torch not installed → keep BM25 ordering/scores
        return [(c, float(c.get("score") or 0.0)) for c in items]

    # Persist best-effort cache (10 min). Ignore cache errors.
    if rc is not None:
        try:
            await rc.setex(key, 600, jsonx.dumps([float(s) for s in scores]))
        except (OSError, RuntimeError, ValueError, TypeError):
            log_stage(
                logger, "resolver", "rerank_cache_set_failed",
                request_id=(current_request_id() or "unknown")
            )

    pairs = list(zip(items, [float(s) for s in scores]))
    pairs.sort(key=lambda t: (-t[1], str(t[0].get("id") or "")))
    return pairs