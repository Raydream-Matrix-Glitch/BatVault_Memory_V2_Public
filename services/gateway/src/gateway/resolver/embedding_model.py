from functools import lru_cache
from typing import List

import numpy as np
import os
from core_config.constants import EMBEDDING_DIM as dim

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # CI / lightweight env
    SentenceTransformer = None  # type: ignore


@lru_cache(maxsize=1)
def _model():
    if SentenceTransformer is None:
        raise RuntimeError("sentence-transformers not installed")
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")


def encode(texts: List[str]) -> np.ndarray:
    if SentenceTransformer is None:
        # fallback zeros with the correct dimension from ENV (default 768)
        return np.zeros((len(texts), dim), dtype=np.float32)
    return _model().encode(texts, normalize_embeddings=True)