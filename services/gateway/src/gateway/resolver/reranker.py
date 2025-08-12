from typing import Dict, List, Tuple
import os
from core_logging import get_logger, log_stage
from shared.content import primary_text_and_field

try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None  # type: ignore

_ce = None
logger = get_logger('gateway')

def _get_model():
    global _ce
    if _ce is None and CrossEncoder is not None:
        # allow override via env (defaulting to the lightweight ms-marco model)
        model_name = os.getenv(
            "CROSS_ENCODER_MODEL",
            "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        _ce = CrossEncoder(model_name)
    return _ce


def rerank(query: str, candidates: List[Dict]) -> List[Tuple[Dict, float]]:
    if CrossEncoder is None:
        return [(c, 0.0) for c in candidates]
    texts = [primary_text_and_field(c) for c in candidates]
    pairs = [(query, t) for (t, _field) in texts]
    # Structured log: which fields were chosen (top-3)
    try:
        from collections import Counter
        field_counts = Counter([f for (_t, f) in texts if f])
        top3 = dict(field_counts.most_common(3))
        log_stage(logger, "resolver", "rerank_pairs_built",
                  field_top3=top3, candidate_count=len(candidates))
    except Exception:
        pass
    scores = _get_model().predict(pairs)
    return sorted(zip(candidates, scores), key=lambda t: t[1], reverse=True)