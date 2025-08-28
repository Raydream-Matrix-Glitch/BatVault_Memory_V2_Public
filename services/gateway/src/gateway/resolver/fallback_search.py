from __future__ import annotations

from typing import Any, Dict, List
import httpx, json, pathlib, re

from core_logging import get_logger, trace_span, log_stage
from core_observability.otel import inject_trace_context
from core_config import get_settings

logger = get_logger("gateway")
settings = get_settings()

# ── Local fixture helper ────────────────────────────────────────────────
def _fixture_decisions_dir() -> pathlib.Path | None:
    for parent in pathlib.Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures" / "decisions"
        if cand.is_dir():
            return cand
    return None

def _offline_fixture_search(text: str, k: int = 24) -> List[Dict[str, Any]]:
    """Deterministic, dependency-free search over local fixture JSON files."""
    repo = _fixture_decisions_dir()
    if repo is None:
        return []

    terms = [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 3]
    if not terms:
        return []

    matches: List[Dict[str, Any]] = []
    for path in repo.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:       # malformed fixture – ignore
            continue
        haystack = f"{doc.get('option','')} {doc.get('rationale','')}".lower()
        score = sum(1 for t in terms if t in haystack)
        if score:
            matches.append(
                {
                    "id": doc.get("id", path.stem),
                    "score": score,
                    "match_snippet": doc.get("rationale", "")[:160],
                }
            )

    matches.sort(key=lambda m: (-m["score"], m["id"]))
    return matches[:k]

# ── Primary search with graceful fallback ──────────────────────────────
async def search_bm25(text: str, k: int) -> List[Dict[str, Any]]:
    """Try Memory-API BM25; if that fails (or returns zero hits), fall back to the local fixtures."""
    payload = {"q": text, "limit": k, "use_vector": False}
    matches: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            # wrap the Memory‑API call in its own span and propagate OTEL headers
            with trace_span("gateway.bm25_search", q=text, limit=k):
                resp = await client.post(
                    f"{settings.memory_api_url}/api/resolve/text",
                    json=payload,
                    headers=inject_trace_context({}),
                )
        if resp.status_code == 200:
            doc = resp.json()
            matches = doc.get("matches", [])
            log_stage(
                logger,
                "gateway",
                "bm25_search_complete",
                match_count=len(matches),
                vector_used=doc.get("vector_used"),
            )
    except Exception:           # network failure / service down
        logger.warning("bm25_search_http_error", exc_info=True)

    if not matches:             # offline back-stop
        matches = _offline_fixture_search(text, k)
        log_stage(
            logger,
            "gateway",
            "bm25_offline_fallback",
            match_count=len(matches),
            vector_used=False,
        )
    return matches

# Back-compat alias
fallback_search = search_bm25