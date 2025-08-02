from __future__ import annotations

# async support for HTTP + jittered sleeps
import asyncio
import httpx
import orjson
import redis
import hashlib
import random
from typing import Optional
from fastapi import HTTPException                       # A-2
from core_logging import get_logger
from core_config import get_settings
from core_models.models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
from .selector import truncate_evidence, bundle_size_bytes
from core_logging import trace_span


# ------------------------------------------------------------------#
#  Constants & helpers                                              #
# ------------------------------------------------------------------#
CACHE_TTL_SEC = 900                           # 15 min (§H3)
# Both alias *and* composite keys must share the same TTL (§11.3).
# We do an ETag freshness check to guard against race conditions.
ALIAS_TPL = "evidence:{anchor_id}:latest"

# ------------------------------------------------------------------ #
#  helpers                                                          #
# ------------------------------------------------------------------ #
def _make_cache_key(
    decision_id: str,
    intent: str,
    graph_scope: str,
    snapshot_etag: str,
    truncation_applied: bool,
) -> str:
    parts = (decision_id, intent, graph_scope, snapshot_etag, str(truncation_applied))
    return "evidence:" + hashlib.sha256("|".join(parts).encode()).hexdigest()

def _collect_allowed_ids(                     # spec §B2 exact-union rule :contentReference[oaicite:2]{index=2}
    anchor: WhyDecisionAnchor,
    events: list[dict],
    trans_pre: list[dict],
    trans_suc: list[dict],
) -> list[str]:
    ids: set[str] = {anchor.id}
    ids.update(e.get("id") for e in events if isinstance(e, dict))
    ids.update(t.get("id") for t in trans_pre + trans_suc if isinstance(t, dict))
    return sorted(i for i in ids if i)


logger = get_logger("evidence_builder")
settings = get_settings()

class EvidenceBuilder:
    """Collect & return a **validated** evidence bundle for *anchor_id*."""

    def __init__(self) -> None:
        # Keep Redis client sync; HTTPX moved to async in build()
        try:
            self._redis: Optional[redis.Redis] = redis.Redis.from_url(settings.redis_url)
        except Exception:  # pragma: no cover
            self._redis = None

    # ------------------------------------------------------------------ #
    #  Public API                                                      #
    # ------------------------------------------------------------------ #
    async def build(self, anchor_id: str) -> WhyDecisionEvidence:
        """
        Two-key Redis cache (§H3), with async HTTP fetch + retry.
           ① alias_key  –›   composite_key
           ② composite_key –› evidence JSON
        Both keys share the same TTL (15 min).
        """
        alias_key   = ALIAS_TPL.format(anchor_id=anchor_id)
        retry_count = 0

        # ---------- fast-path: cache probe BEFORE any network I/O ----------
        if self._redis is not None:
            try:
                composite_key = self._redis.get(alias_key)
                if composite_key:
                    cached = self._redis.get(composite_key)
                    if cached:
                        ev = WhyDecisionEvidence.model_validate_json(cached)
                        if await self._is_fresh(anchor_id, ev.snapshot_etag):
                            ev.__dict__["_retry_count"] = retry_count
                            with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                span.set_attribute("cache.hit", True)
                                span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                            return ev
            except Exception:  # pragma: no cover
                logger.warning("redis read error – bypassing cache", exc_info=True)

        # ---------- cache-miss ➜ full fetch & span chain --------------------
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_expand_ms / 1000.0),
            base_url=settings.memory_api_url,
        )

        with trace_span.ctx("plan", anchor_id=anchor_id):
            plan = {"id": anchor_id, "k": 1}

        with trace_span.ctx("exec", anchor_id=anchor_id) as span_exec:
            resp_neigh = await asyncio.wait_for(
                client.post("/api/graph/expand_candidates", json=plan),
                timeout=settings.timeout_expand_ms / 1000.0,
            )
            span_exec.set_attribute("exec.status_code", resp_neigh.status_code)

        with trace_span.ctx("enrich", anchor_id=anchor_id):
            resp_anchor = await asyncio.wait_for(
                client.get(f"/api/enrich/decision/{anchor_id}"),
                timeout=settings.timeout_search_ms / 1000.0,
            )
            resp_anchor.raise_for_status()
            snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")

            neigh       = resp_neigh.json()
            events      = neigh.get("events", [])
            trans_pre   = neigh.get("preceding", [])
            trans_suc   = neigh.get("succeeding", [])
            anchor      = WhyDecisionAnchor(**resp_anchor.json())

        with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
            ev = WhyDecisionEvidence(
                anchor=anchor,
                events=events,
                transitions=WhyDecisionTransitions(
                    preceding=trans_pre, succeeding=trans_suc
                ),
                allowed_ids=_collect_allowed_ids(anchor, events, trans_pre, trans_suc),
            )
            ev.snapshot_etag            = snapshot_etag
            ev.__dict__["_retry_count"] = retry_count

            # selector truncation (may mutate *ev*)
            ev, selector_meta = truncate_evidence(ev)
            ev.__dict__["_selector_meta"] = selector_meta
            for k, v in selector_meta.items():
                span.set_attribute(k, v)

            span.set_attribute("total_neighbors_found", len(events) + len(trans_pre) + len(trans_suc))
            span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))

        await client.aclose()

        # ---------- cache read (alias ➜ composite ➜ evidence) ----------
        if self._redis is not None:
            try:
                composite_key = self._redis.get(alias_key)
                if composite_key:
                    cached = self._redis.get(composite_key)
                    if cached:
                        ev = WhyDecisionEvidence.model_validate_json(cached)
                        # ---------- ETag freshness check (§11.3) -------------
                        if await self._is_fresh(anchor_id, ev.snapshot_etag):
                            ev.__dict__["_retry_count"] = retry_count
                            logger.debug(
                                "evidence cache hit",
                                extra={
                                    "anchor_id": anchor_id,
                                    "snapshot_etag": ev.snapshot_etag,
                                },
                            )
                            span.set_attribute("cache.hit", True)
                            return ev
                        else:
                            # Stale bundle – fall through and rebuild
                            logger.debug(
                                "evidence cache stale – snapshot_etag changed",
                                extra={
                                    "anchor_id": anchor_id,
                                    "stale_etag": ev.snapshot_etag,
                                },
                            )
            except Exception:
                span.set_attribute("cache.error", True)
                logger.warning("redis read error – bypassing cache", exc_info=True)

        # ---------- cache-miss ➜ async upstream fetch (+1 retry) -------------
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_expand_ms / 1000.0),
            base_url=settings.memory_api_url
        )
        # ── SEARCH stage with timeout (A-2) ───────────────────────────
        for attempt in range(2):
            try:
                resp_anchor = await asyncio.wait_for(
                    client.get(f"/api/enrich/decision/{anchor_id}"),
                    timeout=settings.timeout_search_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="search stage timeout")
            if resp_anchor.status_code == 200:
                retry_count = attempt
                break
            if attempt == 1:
                resp_anchor.raise_for_status()
            await asyncio.sleep(random.uniform(0.05, 0.30))

        resp_anchor.raise_for_status()
        snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")

        # parse anchor
        anchor = WhyDecisionAnchor(**resp_anchor.json())

        # ── EXPAND stage with timeout (A-2) ───────────────────────────
        try:
            resp_neigh = await asyncio.wait_for(
                client.post(
                    "/api/graph/expand_candidates",
                    json={"id": anchor_id, "k": 1},
                ),
                timeout=settings.timeout_expand_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="expand stage timeout")
        resp_neigh.raise_for_status()
        neigh = resp_neigh.json()
        events    = neigh.get("events", [])
        trans_pre = neigh.get("preceding", [])
        trans_suc = neigh.get("succeeding", [])

        await client.aclose()

        # build the Pydantic model
        ev = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(
                preceding=trans_pre, succeeding=trans_suc
            ),
            allowed_ids=_collect_allowed_ids(anchor, events, trans_pre, trans_suc),
        )
        ev.snapshot_etag            = snapshot_etag
        ev.__dict__["_retry_count"] = retry_count

        # ---------------- selector truncation ------------------- #
        ev, selector_meta = truncate_evidence(ev)  # only if > MAX_PROMPT_BYTES (§M4)
        ev.__dict__["_selector_meta"] = selector_meta
        span.set_attribute(
            "selector.truncated", selector_meta.get("selector_truncation", False)
        )
        for k, v in selector_meta.items():
            span.set_attribute(k, v)


        # ---------- cache write (composite + alias) --------------------
        if self._redis is not None:
            try:
                composite_key = _make_cache_key(
                    decision_id=anchor_id,
                    intent="why_decision",
                    graph_scope="k1",
                    snapshot_etag=snapshot_etag,
                    truncation_applied=False,
                )
                ev_json = ev.model_dump_json()
                pipe = self._redis.pipeline()
                ttl = settings.cache_ttl_evidence_sec or CACHE_TTL_SEC
                pipe.setex(alias_key,     ttl, composite_key)
                pipe.setex(composite_key, ttl, ev_json)
                pipe.execute()
            except Exception:
                logger.warning("redis write error", exc_info=True)

        logger.info(
            "evidence_built",
            extra={
                "anchor_id":           anchor_id,
                "total_neighbors":     len(events) + len(trans_pre) + len(trans_suc),
                "bundle_size_bytes":   bundle_size_bytes(ev),
                **selector_meta,
            },
        )
        span.end()
        return ev

    # ------------------------------------------------------------------ #
    #  Snapshot freshness helper                                       #
    # ------------------------------------------------------------------ #
    async def _is_fresh(self, anchor_id: str, cached_etag: str) -> bool:
        """
        True  → cached_etag equals current Memory-API snapshot_etag  
        False → mismatch (or cached_etag=='unknown'); forces rebuild.
        The GET has a hard 50 ms timeout so we stay well under stage SLOs.
        """
        if cached_etag == "unknown":
            return False
        try:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(0.05), base_url=settings.memory_api_url
            )
            resp = await client.get(
                f"/api/enrich/decision/{anchor_id}",
                headers={"x-cache-etag-check": "1"},
            )
            await client.aclose()
            current = resp.headers.get("snapshot_etag", "unknown")
            return current == cached_etag
        except Exception:
            # Fail-open: if the check can’t be completed, treat as fresh.
            return True
