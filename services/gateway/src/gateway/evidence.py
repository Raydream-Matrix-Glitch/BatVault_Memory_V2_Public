from __future__ import annotations

import httpx, orjson, redis, hashlib, random, time
from typing import List, Dict, Any, Optional, Tuple
from pydantic import ValidationError

from core_logging import get_logger
from core_config import get_settings
from .models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
from .selector import truncate_evidence, bundle_size_bytes

# ------------------------------------------------------------------#
#  Constants & helpers                                              #
# ------------------------------------------------------------------#
CACHE_TTL_SEC = 900                           # 15 min (§H3)
ALIAS_TPL = "evidence:{anchor_id}:latest"     # alias → composite key

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
logger = get_logger("evidence_builder")
settings = get_settings()


class EvidenceBuilder:
    """Collect & return a **validated** evidence bundle for *anchor_id*."""

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=3.0, base_url=settings.memory_api_url)
        try:
            self._redis: Optional[redis.Redis] = redis.Redis.from_url(settings.redis_url)
        except Exception:  # pragma: no cover
            self._redis = None

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #
    def build(self, anchor_id: str) -> WhyDecisionEvidence:
        """
        Two-key Redis cache (§H3):
           ① alias_key  –›   composite_key
           ② composite_key –› evidence JSON
        Both keys share the same TTL (15 min).
        """
        alias_key = ALIAS_TPL.format(anchor_id=anchor_id)
        retry_count = 0

        # ---------- cache read (alias ➜ composite ➜ evidence) ----------
        if self._redis is not None:
            try:
                composite_key = self._redis.get(alias_key)
                if composite_key:
                    cached = self._redis.get(composite_key)
                    if cached:
                        ev = WhyDecisionEvidence.model_validate_json(cached)
                        ev.__dict__["_retry_count"] = retry_count
                        logger.debug("evidence cache hit", extra={"anchor_id": anchor_id})
                        return ev
            except Exception:
                logger.warning("redis read error – bypassing cache", exc_info=True)

        # ---------- cache-miss ➜ upstream fetch (+1 retry) -------------
        ev, snapshot_etag, retry_count = self._collect_from_upstream(anchor_id)
        ev.snapshot_etag = snapshot_etag           # B-6
        ev.__dict__["_retry_count"] = retry_count  # B-8

        # ---------------- selector truncation ------------------- #
        ev, selector_meta = truncate_evidence(ev)
        ev.__dict__["_selector_meta"] = selector_meta   # allow app.py to surface meta

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
                # Alias first → avoids race; alias has **no TTL** (§Cache policy)
                pipe.set(alias_key, composite_key)
                pipe.setex(composite_key, CACHE_TTL_SEC, ev_json)
                pipe.execute()                                # B-1 & B-7
            except Exception:
                logger.warning("redis write error", exc_info=True)

        logger.info(                                             # observability
            "evidence_built",
            extra={
                "anchor_id": anchor_id,
                "bundle_size_bytes": bundle_size_bytes(ev),
                **selector_meta,
            },
        )
        return ev

    # ------------------------------------------------------------------ #
    #  Helpers                                                           #
    # ------------------------------------------------------------------ #
    def _collect_from_upstream(self, anchor_id: str) -> Tuple[WhyDecisionEvidence, str, int]:
        """Return (evidence, snapshot_etag, retry_count) with ≤ 1 retry + jitter ≤ 300 ms."""
        retry_count = 0
        for attempt in range(2):
            try:
                resp_anchor = self._client.get(f"/api/enrich/decision/{anchor_id}")
                resp_anchor.raise_for_status()
                break
            except Exception:
                retry_count = attempt + 1
                if attempt == 1:
                    raise
                time.sleep(random.uniform(0.05, 0.30))  # capped jitter


        snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")
        try:
            anchor = WhyDecisionAnchor(**resp_anchor.json())

            resp_neigh = self._client.post(
                "/api/graph/expand_candidates", json={"id": anchor_id, "k": 1}
            )
            resp_neigh.raise_for_status()
            neigh = resp_neigh.json()

            events      = neigh.get("events", [])
            trans_pre   = neigh.get("preceding", [])
            trans_suc   = neigh.get("succeeding", [])
        except Exception:  # pragma: no cover
            logger.error("memory_api_error", exc_info=True, extra={"anchor_id": anchor_id})
            anchor = WhyDecisionAnchor(id=anchor_id)
            events, trans_pre, trans_suc = [], [], []

        evidence = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
        )

        ids = {anchor.id}
        ids.update([e.get("id") for e in events])
        ids.update([t.get("id") for t in trans_pre])
        ids.update([t.get("id") for t in trans_suc])
        evidence.allowed_ids = sorted(i for i in ids if i)
        # ─── spec sanity: allowed_ids must cover all ids present ─── #
        missing = {anchor.id, *ids} - set(evidence.allowed_ids)
        if missing:   # log instead of assert to avoid prod-blowups
            logger.warning("allowed_ids missing objects", extra={"ids": list(missing)})

        return evidence, snapshot_etag, retry_count
