from typing import Any, Dict, List
import httpx

from core_config import get_settings

settings = get_settings()


async def search_bm25(text: str, k: int = 24) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.post(f"{settings.memory_api_url}/api/resolve/text",
                                     json={"query_text": text, "k": k})
        return resp.json().get("matches", [])
    except Exception:
        return []