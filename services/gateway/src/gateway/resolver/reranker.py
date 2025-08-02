from typing import Dict, List, Tuple
import os

try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None  # type: ignore

_ce = None

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
    pairs = [(query, c.get("text") or c.get("rationale") or "") for c in candidates]
    scores = _get_model().predict(pairs)
    return sorted(zip(candidates, scores), key=lambda t: t[1], reverse=True)