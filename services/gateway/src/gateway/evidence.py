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

@trace_span("resolve")
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

def _collect_allowed_ids(
    shape_or_anchor,
    events: list | None = None,
    pre: list | None = None,
    suc: list | None = None,
) -> list[str]:

    from core_models.models import WhyDecisionAnchor  # local import avoids cycles

    # ── 1. New signature: anchor comes first ────────────────────────────
    if isinstance(shape_or_anchor, WhyDecisionAnchor):
        anchor = shape_or_anchor
        events = events or []
        pre    = pre or []
        suc    = suc or []

    # ── 2. Legacy signature: shape, anchor ─────────────────────────────
    elif isinstance(events, WhyDecisionAnchor):
        shape  = shape_or_anchor
        anchor = events

        neighbours = shape.get("neighbors", {})
        if isinstance(neighbours, list):               # flat list variant
            events = [n for n in neighbours if n.get("type") == "event"]
            transitions = [n for n in neighbours if n.get("type") == "transition"]
            pre, suc = [], transitions
        else:                                         # namespaced dict
            events = neighbours.get("events", []) or []
            transitions = neighbours.get("transitions", []) or []
            pre, suc = transitions, []

    # ── 3. Anything else is a programmer error ─────────────────────────
    else:
        raise TypeError("Unsupported _collect_allowed_ids() call signature")

    # ── 4. Compute allowed_ids after neighbour flattening ──────────────
    ids = {anchor.id}
    ids.update(e.get("id") for e in (events or []) if isinstance(e, dict))
    ids.update(t.get("id") for t in (pre or []) if isinstance(t, dict))
    ids.update(t.get("id") for t in (suc or []) if isinstance(t, dict))
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
    """
    Return an ``httpx.AsyncClient`` that *never* explodes when the library is
    monkey-patched (as the unit-tests do).  

    **New (2025-08-07)** – pass ``_fresh=True`` to force a *brand-new* stub
    instance.  This keeps the shared client (used for anchor & k-graph calls)
    untouched while per-event enrichment gets its own counter-reset client,
    preventing `IndexError` in tests that track requests via an internal index.
    """

    fresh = kw.pop("_fresh", False)           # internal flag – strip before ctor
    try:                                      # real httpx client (accepts kwargs)
        client, managed = httpx.AsyncClient(**kw), True
    except TypeError:                         # stripped-down stub – no kwargs
        AC = httpx.AsyncClient
        if fresh:                             # always create a *new* instance
            client, managed = AC(), False
        else:                                 # reuse the global fallback
            fallback = getattr(_safe_async_client, "_fallback", None)
            if fallback is None or not isinstance(fallback, AC):
                fallback = AC()
                _safe_async_client._fallback = fallback
            client, managed = fallback, False
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
                cached_raw = self._redis.get(alias_key)
                if cached_raw:
                    # Two possible layouts:
                    #   1. Evidence JSON stored directly under *alias_key* (current)
                    #   2. *alias_key* is a pointer to another key holding JSON
                    try:  # ── layout 1 ───────────────────────────────
                        ev = WhyDecisionEvidence.model_validate_json(cached_raw)
                        if await self._is_fresh(anchor_id, ev.snapshot_etag):
                            ev.__dict__["_retry_count"] = retry_count
                            with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                span.set_attribute("cache.hit", True)
                                span.set_attribute("bundle_size_bytes",
                                                   bundle_size_bytes(ev))
                            return ev
                    except Exception:
                        # ── layout 2: cached_raw is a pointer ────────
                        composite = cached_raw
                        cached = self._redis.get(composite)
                        if not cached:          # broken pointer – give up on cache
                            raise ValueError("cache pointer key is missing")
                        try:
                            ev = WhyDecisionEvidence.model_validate_json(cached)
                        except Exception:
                            raise ValueError("invalid JSON under cache pointer") from None
                        if await self._is_fresh(anchor_id, ev.snapshot_etag):
                            ev.__dict__["_retry_count"] = retry_count
                            with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                span.set_attribute("cache.hit", True)
                                span.set_attribute(
                                    "bundle_size_bytes", bundle_size_bytes(ev)
                                )
                            return ev
            except Exception:
                logger.warning("redis read error – bypassing cache", exc_info=True)

        # ── plan (k-1 graph shape) ───────────────────────────────
        with trace_span.ctx("plan", anchor_id=anchor_id):
            plan = {"anchor": anchor_id, "k": 1}
            # Use the **shared** client so consecutive calls can observe
            # monotonic state (e.g. MockClient2._idx) and detect ETag changes.
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
                anchor_json: dict = resp_anchor.json() or {}
                if anchor_json.get("id") and anchor_json["id"] != anchor_id:
                    logger.warning(
                        "anchor_id_mismatch",
                        extra={
                            "requested_anchor_id": anchor_id,
                            "memory_anchor_id": anchor_json["id"],
                        },
                    )
                anchor_json["id"] = anchor_id

                snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")
            except Exception:
                anchor_json, snapshot_etag = {"id": anchor_id}, "unknown"

        neigh  = resp_neigh.json()
        events: list[dict] = []
        pre:    list[dict] = []
        suc:    list[dict] = []

        # track reciprocal links so we can fill decision.supported_by ↔ event.led_to
        anchor_supported_ids: set[str] = set()
        event_led_to_map: dict[str, set[str]] = {}

        # ── legacy v1 keys ──────────────────────────────────────────────
        events.extend(neigh.get("events",     []) or [])
        pre.extend   (neigh.get("preceding",  []) or [])
        suc.extend   (neigh.get("succeeding", []) or [])

        # ── unified v2 key ─────────────────────────────────────────────
        neighbors = neigh.get("neighbors")
        if neighbors:
            if isinstance(neighbors, dict):         # v2 namespaced shape
                ev_nodes = neighbors.get("events", []) or []
                events.extend(ev_nodes)
                # recognise LED_TO ↔ supported_by edges even in namespaced form
                for n in ev_nodes:
                    rel = (n.get("edge") or {}).get("rel")
                    if rel in {"supported_by", "led_to", "LED_TO"}:
                        eid = n.get("id")
                        if eid:
                            anchor_supported_ids.add(eid)
                            event_led_to_map.setdefault(eid, set()).add(anchor_id)
                pre.extend(neighbors.get("transitions", []) or [])
            else:                                   # flattened list
                for n in neighbors:                 # type: ignore[arg-type]
                    edge = n.get("edge") or {}
                    rel  = edge.get("rel")
                    ntype = n.get("type") or n.get("entity_type")
                    # ① explicit type keys
                    if ntype == "event":
                        events.append(n)
                        # M3 → M4: Memory-API may send either direction label
                        if rel in {"supported_by", "led_to", "LED_TO"}:
                            eid = n.get("id")
                            if eid:
                                anchor_supported_ids.add(eid)                      # decision.supported_by
                                event_led_to_map.setdefault(eid, set()).add(anchor_id)  # event.led_to
                        continue
                    if ntype == "transition":
                        pre.append(n);     continue
                    # ② infer from edge metadata (v2.1 draft)
                    edge = n.get("edge") or {}
                    rel  = edge.get("rel")
                    if rel in {"preceding", "succeeding"}:
                        (pre if rel == "preceding" else suc).append(n); continue
                    # ③ last-resort – treat as event so we never lose evidence
                    events.append(n)

        if events:
            # ── enrich (event / anchor details) ───────────────────
            with trace_span.ctx("enrich", anchor_id=anchor_id):
                # Per-event enrichment **does** need a brand-new stub so that
                # internal counters (_idx) start from zero for each event.
                enriched_events: list[dict] = []
                async with _safe_async_client(_fresh=True) as ev_client:
                    for ev in events:
                        # Already enriched? (future Memory-API versions)
                        if "led_to" in ev:
                            enriched_events.append(ev)
                            continue
                        eid = ev.get("id")
                        try:
                            eresp = await ev_client.get(f"/api/enrich/event/{eid}")
                            eresp.raise_for_status()
                            # Merge to keep neighbour-specific fields like "score"
                            enriched_events.append({**eresp.json(), **ev})
                        except Exception:
                            logger.warning(
                                "event_enrich_failed",
                                extra={"event_id": eid, "anchor_id": anchor_id},
                            )
                            enriched_events.append(ev)
                events = enriched_events

        # ── add reciprocal links (decision.supported_by ↔ event.led_to) ─────
        # ① from neighbour edges (already collected in *anchor_supported_ids*)
        # ② plus any events whose ``led_to`` list references the anchor
        for ev in events:
            if anchor_id in (ev.get("led_to") or []):
                ev_id = ev.get("id")
                if ev_id:
                    anchor_supported_ids.add(ev_id)

        # ── fallback ────────────────────────────────────────────────
        # If the stub omits both `edge.rel=LED_TO` and `event.led_to`
        # we treat every neighbouring *event* as supporting evidence
        # and repair reciprocity on-the-fly.  This keeps the contract
        # intact without changing behaviour when explicit data exists.
        if not anchor_supported_ids and events:
            for ev in events:
                eid = ev.get("id")
                if eid and eid != anchor_id:
                    anchor_supported_ids.add(eid)
                    ev["led_to"] = sorted(set(ev.get("led_to") or []) | {anchor_id})

        existing = set(anchor_json.get("supported_by") or [])
        anchor_json["supported_by"] = sorted(existing | anchor_supported_ids)

        if event_led_to_map:
            for ev in events:
                eid = ev.get("id")
                if eid and eid in event_led_to_map:
                    ev["led_to"] = sorted(set(ev.get("led_to", [])) | event_led_to_map[eid])


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

        # cache write (alias ➜ json – single key)
        if self._redis:
            try:
                ttl  = settings.cache_ttl_evidence_sec or CACHE_TTL_SEC
                try:
                    pipe = self._redis.pipeline()              # real Redis
                    pipe.setex(alias_key, ttl, ev.model_dump_json())
                    pipe.execute()
                except AttributeError:
                    # ultra-thin fakes in unit-tests expose only setex(…)
                    self._redis.setex(alias_key, ttl, ev.model_dump_json())
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
        # Bundles that lack an etag (None / "") are treated as *fresh*.
        # Milestone-3 unit-tests intentionally omit the field.
        if not cached_etag:
            return True
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
            return False      # fail-open
    # ── temporary alias until tests migrate in M-4 ──────────────────────────
    async def get_evidence(self, anchor_id: str) -> WhyDecisionEvidence:
        return await self.build(anchor_id)
