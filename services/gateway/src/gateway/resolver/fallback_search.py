from __future__ import annotations
from typing import Any, Dict, List
import os
import pathlib
import re
from core_utils import jsonx
from core_logging import get_logger, trace_span, log_stage
from core_config import get_settings
from core_utils.domain import storage_key_to_anchor
from core_http.client import fetch_json
import httpx

logger = get_logger("gateway.resolver.fallback_search")
settings = get_settings()


def _normalize_anchor_id(candidate: str) -> str:
    """Canonicalize IDs to '<domain>#<id>' using core mapping."""
    s = str(candidate or "")
    return s if "#" in s else storage_key_to_anchor(s)

# ── Primary search with graceful fallback ──────────────────────────────
async def search_bm25(
    text: str,
    k: int,
    *,
    request_id: str | None = None,
    snapshot_etag: str | None = None,
    policy_headers: dict | None = None,
) -> List[Dict[str, Any]]:
    """Resolve text to ranked anchors via Memory-API BM25 using core HTTP client (no silent fallbacks)."""
    payload = {"q": text, "limit": k, "use_vector": False}
    matches: List[Dict[str, Any]] = []
    # Wrap the Memory-API call in its own span; let core client inject OTEL.
    with trace_span("gateway.bm25_search", logger=logger, q=text, limit=k):
        try:
            doc, headers = await fetch_json(
                "POST",
                f"{settings.memory_api_url}/api/resolve/text",
                json=payload,
                headers=dict(policy_headers or {}),
                stage="search",
                request_id=request_id,
                return_headers=True,
            )
            etag = (headers or {}).get("x-snapshot-etag") or snapshot_etag
            log_stage(logger, "resolver", "bm25_http_status", status_code=200, request_id=request_id)
            matches = (doc or {}).get("matches", []) if isinstance(doc, dict) else []
            log_stage(
                logger, "resolver", "bm25_search_complete",
                match_count=len(matches), vector_used=bool((doc or {}).get("vector_used")),
                request_id=request_id, snapshot_etag=etag,
            )
        except httpx.HTTPStatusError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            body = {}
            try:
                resp = getattr(e, "response", None)
                body = jsonx.loads(resp.content) if (resp and resp.content) else {}
            except Exception:
                body = {}
            log_stage(logger, "resolver", "bm25_http_status", status_code=status, request_id=request_id)
            if status == 409:
                matches = body.get("candidates", []) or []
                log_stage(logger, "resolver", "bm25_search_ambiguous", request_id=request_id, snapshot_etag=snapshot_etag)
            elif status == 404:
                matches = []
                log_stage(
                    logger, "resolver", "bm25_search_zero",
                    match_count=0, vector_used=False,
                    request_id=request_id, snapshot_etag=snapshot_etag,
                )
            else:
                logger.warning("bm25_search_http_error", exc_info=True)
                matches = []
        except httpx.HTTPError:
            logger.warning("bm25_search_http_error", exc_info=True)
            matches = []

    # Normalize anchor IDs to <domain>#<id> (deterministic contract)
    _normd = []
    for m in (matches or []):
        _id_raw = m.get("id")
        _id_norm = _normalize_anchor_id(_id_raw) if isinstance(_id_raw, str) else _id_raw
        if isinstance(_id_raw, str) and isinstance(_id_norm, str) and _id_raw != _id_norm:
            log_stage(logger, "resolver", "id_normalized", request_id=request_id, before=_id_raw, after=_id_norm)
        if isinstance(_id_norm, str):
            m = dict(m); m["id"] = _id_norm
        _normd.append(m)
    matches = _normd
    # Allow DECISION and EVENT anchors (v3): anchors are a capability, not a class.
    # Drop non-DECISION/EVENT candidates early to avoid selecting unrelated docs.
    matches = [m for m in (matches or []) if str((m or {}).get("type", "")).upper() in {"DECISION","EVENT"}]

    # Optional: dev-only offline fixtures when explicitly allowed
    if not matches and os.getenv("ALLOW_OFFLINE_FIXTURES", "0") == "1":
        try:
            import asyncio
            from pathlib import Path
            import re as _re

            def _fixture_dir() -> Path | None:
                for parent in Path(__file__).resolve().parents:
                    cand = parent / "memory" / "fixtures" / "decisions"
                    if cand.is_dir():
                        return cand
                return None

            repo = _fixture_dir()
            if repo:
                # Respect baseline: disabled by default; enable only with DEV_RESOLVER_OFFLINE_FIXTURES=1
                if not str(os.getenv('DEV_RESOLVER_OFFLINE_FIXTURES', '0')).lower() in ('1','true','yes'):
                    log_stage(logger, "resolver", "bm25_offline_skipped", request_id=request_id)
                    raise RuntimeError('offline_fixtures_disabled')
                terms = [t for t in _re.findall(r"\w+", text.lower()) if len(t) >= 3]

                def _search() -> List[Dict[str, Any]]:
                    out: List[Dict[str, Any]] = []
                    for p in repo.glob("*.json"):
                        try:
                            doc = jsonx.loads(p.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        hay = f"{doc.get('option', '')} {doc.get('rationale', '')}".lower()
                        s = sum(1 for t in terms if t in hay) if terms else 0
                        if s:
                            out.append({
                                "id": doc.get("id", p.stem),
                                "score": s,
                                "match_snippet": doc.get("rationale", "")[:160],
                            })
                    out.sort(key=lambda m: (-m["score"], m["id"]))
                    return out[:k]

            matches = await asyncio.to_thread(_search)
            log_stage(
                logger, "resolver", "bm25_offline_fallback",
                match_count=len(matches),
                vector_used=False,
                request_id=request_id,
                snapshot_etag=snapshot_etag,
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            # keep silent in dev path to avoid interfering with 404/409 selection
            matches = matches or []
    return matches

# Back-compat alias
fallback_search = search_bm25