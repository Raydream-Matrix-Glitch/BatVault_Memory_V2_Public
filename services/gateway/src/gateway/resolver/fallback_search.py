from typing import Any, Dict, List
import httpx
from core_logging import get_logger, trace_span, log_stage
from core_config import get_settings

logger = get_logger("gateway")
settings = get_settings()
        
async def search_bm25(text: str, k: int) -> List[Dict[str, Any]]:
    """Fallback BM25 search against Memory-API (non-vector)."""
    payload = {"q": text, "limit": k, "use_vector": False}
    async with httpx.AsyncClient(timeout=0.8) as client:
        with trace_span("gateway.bm25_search", q=text, limit=k):
            resp = await client.post(
                f"{settings.memory_api_url}/api/resolve/text", json=payload
            )
    doc = resp.json()
    log_stage(
        logger,
        "gateway",
        "bm25_search_complete",
        match_count=len(doc.get("matches", [])),
        vector_used=doc.get("vector_used")
    )
    return doc.get("matches", [])

# ---------------------------------------------------------------------------
# Back-compat: anything that still imports `fallback_search` will continue to
# work after this rename. Remove the alias once all usages are updated.
fallback_search = search_bm25