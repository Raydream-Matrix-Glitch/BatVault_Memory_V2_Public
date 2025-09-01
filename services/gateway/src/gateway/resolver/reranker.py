from typing import Dict, List, Tuple
from core_ml.rerank import rerank as _rerank

async def rerank(query: str, candidates: List[Dict]) -> List[Tuple[Dict, float]]:
    """
    Proxy to the shared, pluggable reranker.  Delegates to the asynchronous
    ``core_ml.rerank.rerank`` coroutine and must itself be awaited.
    """
    return await _rerank(query, candidates)