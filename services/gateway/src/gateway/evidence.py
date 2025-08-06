"""
evidence.py – build WhyDecisionEvidence bundles
───────────────────────────────────────────────
1. Imports
2. Config & constants
3. Public stubs (resolve_anchor, expand_graph)
4. Helper utilities
5. EvidenceBuilder class
"""

# 1 ─────────────────────────── Imports ────────────────────────────────
from __future__ import annotations

import asyncio, hashlib, inspect, random, httpx
from contextlib import asynccontextmanager, contextmanager
from typing import Optional

import httpx, redis
from fastapi import HTTPException

from core_config import get_settings
from core_logging import get_logger, trace_span
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
)
from .selector import truncate_evidence, bundle_size_bytes

# 2 ───────────── Config & constants ───────────────────────────────────
settings        = get_settings()
logger          = get_logger("gateway.evidence")

CACHE_TTL_SEC   = 900          # 15 min
ALIAS_TPL       = "evidence:{anchor_id}:latest"

# 3 ───────────── Public test stubs ────────────────────────────────────
__all__ = [
    "resolve_anchor",
    "expand_graph",
    "WhyDecisionEvidence",
    "_collect_allowed_ids",
]

async def resolve_anchor(decision_ref: str, *, intent: str | None = None):
    await asyncio.sleep(0)
    return {"id": decision_ref}

async def expand_graph(decision_id: str, *, intent: str | None = None, k: int = 1):
    settings = get_settings()
    url      = f"{settings.memory_api_url.rstrip('/')}/api/graph/expand_candidates"
    payload  = {"anchor": decision_id, "k": k}

    timeout_s = 0.25
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

# 4 ─────────────────────── Helpers ────────────────────────────────────
def _make_cache_key(decision_id: str, intent: str, scope: str,
                    etag: str, truncated: bool) -> str:
    raw = "|".join((decision_id, intent, scope, etag, str(truncated)))
    return "evidence:" + hashlib.sha256(raw.encode()).hexdigest()

def _collect_allowed_ids(                       # backwards-compat shim
    shape_or_anchor,
    events: list | None = None,
    pre: list | None = None,
    suc: list | None = None,
) -> list[str]:
    if events is None:                          # ── legacy 2-arg call ──
        shape, anchor = shape_or_anchor, pre    # type: ignore
        neigh = shape.get("neighbors", {})
        if isinstance(neigh, list):             # flat list
            events = [n for n in neigh if n.get("type") == "event"]
            trans  = [n for n in neigh if n.get("type") == "transition"]
            pre, suc = [], trans
        else:                                   # namespaced dict
            events = neigh.get("events", [])
            trans  = neigh.get("transitions", [])
            pre, suc = trans, []
    else:                                       # ── new 4-arg call ──
        anchor = shape_or_anchor                # type: ignore

    ids = {anchor.id}
    ids.update(e.get("id") for e in (events or []) if isinstance(e, dict))
    ids.update(t.get("id") for t in (pre or []) + (suc or []) if isinstance(t, dict))
    return sorted(i for i in ids if i)

# trace-span fallback (unit-tests monkey-patch the real one)
if not hasattr(trace_span, "ctx"):
    @contextmanager
    def _noop_ctx(_stage: str, **_kw):
        class _Span:
            def set_attribute(self, *_a, **_k): ...
            def end(self): ...
        yield _Span()
    trace_span.ctx = _noop_ctx            # type: ignore[attr-defined]

@asynccontextmanager
async def _safe_async_client(**kw):
    """Tolerates stripped-down httpx stubs used in tests."""
    try:
        client, managed = httpx.AsyncClient(**kw), True
    except TypeError:
        client, managed = httpx.AsyncClient(), False
    try:
        if managed and hasattr(client, "__aenter__"):
            async with client: yield client
        else:
            yield client
    finally:
        if not managed and hasattr(client, "aclose"):
            await client.aclose()

# 5 ──────────────── EvidenceBuilder ───────────────────────────────────
class EvidenceBuilder:
    """Collect and cache a WhyDecisionEvidence bundle for *anchor_id*."""

    def __init__(self, *, redis_client: Optional[redis.Redis] = None):
        if redis_client is not None:
            self._redis = redis_client
        else:
            try:
                self._redis = redis.Redis.from_url(settings.redis_url)
            except Exception:
                self._redis = None

    # ───────────────────────── public API ─────────────────────────────
    async def build(self, anchor_id: str) -> WhyDecisionEvidence:
        alias_key   = ALIAS_TPL.format(anchor_id=anchor_id)
        retry_count = 0

        # fast-path ─ try cache before network I/O
        if self._redis:
            try:
                composite = self._redis.get(alias_key)
                if composite:
                    cached = self._redis.get(composite)
                    if cached:
                        ev = WhyDecisionEvidence.model_validate_json(cached)
                        if await self._is_fresh(anchor_id, ev.snapshot_etag):
                            ev.__dict__["_retry_count"] = retry_count
                            with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                span.set_attribute("cache.hit", True)
                                span.set_attribute("bundle_size_bytes",
                                                   bundle_size_bytes(ev))
                            return ev
            except Exception:
                logger.warning("redis read error – bypassing cache", exc_info=True)

        # plan & exec (Memory-API)
        plan = {"anchor": anchor_id, "k": 1}
        async with _safe_async_client(
            timeout=settings.timeout_expand_ms / 1000.0,
            base_url=settings.memory_api_url,
        ) as client:
            with trace_span.ctx("exec", anchor_id=anchor_id):
                resp_neigh = await asyncio.wait_for(
                    client.post("/api/graph/expand_candidates", json=plan),
                    timeout=settings.timeout_expand_ms / 1000.0,
                )

            # anchor enrich (fail-soft)
            try:
                resp_anchor = await asyncio.wait_for(
                    client.get(f"/api/enrich/decision/{anchor_id}"),
                    timeout=settings.timeout_search_ms / 1000.0,
                )
                resp_anchor.raise_for_status()
                anchor_json  = resp_anchor.json()
                snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")
            except Exception:
                anchor_json, snapshot_etag = {"id": anchor_id}, "unknown"

        neigh  = resp_neigh.json()

        # Merge evidence from every contract variant we recognise so that
        # any future additive change is *automatically* included.
        events: list[dict] = []
        pre:    list[dict] = []
        suc:    list[dict] = []

        # ── legacy v1 keys ──────────────────────────────────────────────
        events.extend(neigh.get("events",     []) or [])
        pre.extend   (neigh.get("preceding",  []) or [])
        suc.extend   (neigh.get("succeeding", []) or [])

        # ── unified v2 key ─────────────────────────────────────────────
        neighbors = neigh.get("neighbors")
        if neighbors:
            if isinstance(neighbors, dict):         # v2 namespaced shape
                events.extend(neighbors.get("events",        []) or [])
                pre.extend   (neighbors.get("transitions",   []) or [])
            else:                                   # flattened list
                for n in neighbors:                 # type: ignore[arg-type]
                    ntype = n.get("type") or n.get("entity_type")
                    # ① explicit type keys
                    if ntype == "event":
                        events.append(n);  continue
                    if ntype == "transition":
                        pre.append(n);     continue
                    # ② infer from edge metadata (v2.1 draft)
                    edge = n.get("edge") or {}
                    rel  = edge.get("rel")
                    if rel in {"preceding", "succeeding"}:
                        (pre if rel == "preceding" else suc).append(n); continue
                    # ③ last-resort – treat as event so we never lose evidence
                    events.append(n)

#        if not events and anchor_json.get("supported_by"):
#            async with _safe_async_client(
#                timeout=settings.timeout_search_ms / 1000.0,
#                base_url=settings.memory_api_url,
#            ) as client:
#                for eid in anchor_json["supported_by"]:
#                    try:
#                        eresp = await client.get(f"/api/enrich/event/{eid}")
#                        eresp.raise_for_status()
#                        events.append(eresp.json())
#                    except Exception:
#                        logger.warning("event_enrich_failed",
#                                       extra={"event_id": eid,
#                                              "anchor_id": anchor_id})

        anchor = WhyDecisionAnchor(**anchor_json)
        ev = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(preceding=pre, succeeding=suc),
            allowed_ids=_collect_allowed_ids(anchor, events, pre, suc),
        )

        ev.snapshot_etag = snapshot_etag
        ev.__dict__["_retry_count"] = retry_count

        # selector truncation (if > MAX_PROMPT_BYTES)
        ev, selector_meta = truncate_evidence(ev)
        ev.__dict__["_selector_meta"] = selector_meta

        # cache write (alias ➜ composite ➜ json)
        if self._redis:
            try:
                composite = _make_cache_key(anchor_id, "why_decision", "k1",
                                            snapshot_etag, False)
                ttl  = settings.cache_ttl_evidence_sec or CACHE_TTL_SEC
                try:
                    pipe = self._redis.pipeline()              # real Redis
                    pipe.setex(alias_key, ttl, composite)
                    pipe.setex(composite, ttl, ev.model_dump_json())
                    pipe.execute()
                except AttributeError:
                    # ultra-thin fakes in unit-tests expose only setex(…)
                    self._redis.setex(alias_key, ttl, composite)
                    self._redis.setex(composite, ev.model_dump_json())
            except Exception:
                logger.warning("redis write error", exc_info=True)

        logger.info("evidence_built", extra={
            "anchor_id": anchor_id,
            "bundle_size_bytes": bundle_size_bytes(ev),
            **selector_meta,
        })
        return ev

    # ─────────────────────── internal helper ──────────────────────────
    async def _is_fresh(self, anchor_id: str, cached_etag: str) -> bool:
        """Check if cached snapshot_etag is still current (50 ms budget)."""
        if cached_etag == "unknown":
            return False
        try:
            async with _safe_async_client(
                timeout=0.05, base_url=settings.memory_api_url
            ) as client:
                resp = await client.get(
                    f"/api/enrich/decision/{anchor_id}",
                    headers={"x-cache-etag-check": "1"},
                )
            return resp.headers.get("snapshot_etag", "unknown") == cached_etag
        except Exception:
            return True      # fail-open
    # ── temporary alias until tests migrate in M-4 ──────────────────────────
    async def get_evidence(self, anchor_id: str) -> WhyDecisionEvidence:
        return await self.build(anchor_id)
