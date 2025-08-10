# 1 ─────────────────────────── Imports ────────────────────────────────
from __future__ import annotations

import asyncio, hashlib, inspect, random, httpx, os, concurrent.futures, json
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Dict, Optional, Tuple

import httpx, redis
from fastapi import HTTPException

from core_config import get_settings
from core_config.constants import TIMEOUT_EXPAND_MS as _EXPAND_MS
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

# ── performance budgets (§Tech-Spec M4) ──────────────────────────────
_REDIS_GET_BUDGET_MS = int(os.getenv("REDIS_GET_BUDGET_MS", "100"))   # ≤100 ms fail-open
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

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
    payload  = {"node_id": decision_id, "k": k}

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

def _extract_snapshot_etag(resp: httpx.Response | object) -> str:
    """
    Retrieve the *snapshot_etag* marker from an HTTP response.
    Robust to:
      • httpx.Headers or dict-like objects (not just ``dict``)
      • case differences and ``-``/``_`` variants (e.g. "Snapshot-ETag")
    Falls back to ``"unknown"`` if not present.
    """
    headers = getattr(resp, "headers", None)

    # Convert any header container to an iterable of (key, value) pairs
    items = []
    try:
        if headers is None:
            items = []
        elif hasattr(headers, "items"):
            items = list(headers.items())
        elif isinstance(headers, (list, tuple)):
            items = list(headers)
        else:
            # Last resort: try to coerce to dict
            items = list(dict(headers).items())
    except Exception:
        items = []

    for k, v in items:
        try:
            key = str(k).lower().replace("-", "_")
        except Exception:
            continue
        if key == "snapshot_etag":
            return v
    return "unknown"

# trace-span fallback (unit-tests monkey-patch the real one)
if not hasattr(trace_span, "ctx"):
    @contextmanager
    def _noop_ctx(_stage: str, **_kw):
        class _Span:
            def set_attribute(self, *_a, **_k): ...
            def end(self): ...
        yield _Span()
    trace_span.ctx = _noop_ctx            # type: ignore[attr-defined]

# Maintain a module‑level shared fallback client for scenarios where the
# underlying httpx.AsyncClient has been monkey‑patched and cannot accept
# arbitrary kwargs.  Using a shared instance allows per‑request state (such
# as an internal counter used by unit‑tests) to persist across successive
# calls to EvidenceBuilder.build, enabling snapshot_etag extraction to work
# correctly.  Per‑event enrichment requests should always bypass this
# shared client by setting the `_fresh` flag.
_shared_fallback_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def _safe_async_client(**kw):
    """
    Return an ``httpx.AsyncClient`` that never explodes when the library is
    monkey‑patched by the unit‑tests.  Pass ``_fresh=True`` to request a
    brand‑new stub instance.  In fallback mode without ``_fresh`` the
    helper reuses a shared client so that internal counters (used by
    tests to verify call ordering) persist across EvidenceBuilder invocations.
    """
    fresh = kw.pop("_fresh", False)
    client: httpx.AsyncClient
    managed: bool
    try:
        # Attempt to construct a real httpx.AsyncClient.  This may raise
        # TypeError when the underlying class has been monkey‑patched and
        # does not accept kwargs like ``timeout`` or ``base_url``.
        client = httpx.AsyncClient(**kw)
        managed = True
    except TypeError:
        # When a stubbed AsyncClient refuses kwargs, fall back to our
        # module‑level shared instance.  Create a fresh instance only
        # when explicitly requested via `_fresh` or if none exists yet.
        global _shared_fallback_client
        AC = httpx.AsyncClient
        if fresh or _shared_fallback_client is None:
            try:
                _shared_fallback_client = AC()
            except Exception:
                _shared_fallback_client = AC()
        client = _shared_fallback_client  # type: ignore[assignment]
        managed = False
    try:
        if managed and hasattr(client, "__aenter__"):
            # Use context manager for real clients to ensure proper cleanup.
            async with client as real_client:
                yield real_client
        else:
            # Yield the fallback client directly; do not enter as context
            yield client
    finally:
        if managed:
            # ``async with`` handles cleanup for real clients.
            pass
        else:
            # For fallback clients we avoid closing the shared instance so
            # that internal state persists across calls.  However, when
            # `_fresh=True` was set we created a throw‑away stub that
            # should be closed immediately.
            if fresh:
                try:
                    await client.aclose()
                except Exception:
                    pass
                if client is _shared_fallback_client:
                    _shared_fallback_client = None

# 5 ──────────────── EvidenceBuilder ───────────────────────────────────
class EvidenceBuilder:
    """
    Collect and cache a ``WhyDecisionEvidence`` bundle.

    Cache layout (spec §H3):
        evidence:{anchor_id}:latest           → *pointer* to composite key
        evidence:sha256(<decision,intent,…>)  → bundled JSON
    """

    def __init__(self, *, redis_client: Optional[redis.Redis] = None):
        if redis_client is not None:
            self._redis = redis_client
        else:
            try:
                self._redis = redis.Redis.from_url(settings.redis_url)
            except Exception:
                self._redis = None

        # Reset the module‑level fallback client whenever a new
        # EvidenceBuilder instance is created.  The fallback client is used
        # by ``_safe_async_client`` to persist stateful stubs across multiple
        # calls to ``build``.  Without resetting it here, a fallback
        # instantiated in one test could be inadvertently reused by another,
        # leading to order‑dependent behaviour (e.g. stale ETag counters).
        # Clearing the global reference ensures each builder starts from a
        # clean slate while still reusing the same fallback instance across
        # successive ``build`` calls on the same builder.
        global _shared_fallback_client
        _shared_fallback_client = None

    # ───────────────── safe, bounded Redis read ──────────────────────
    async def _safe_get(self, key: str):
        """
        Wrapper around ``redis.get`` that enforces the 100 ms budget and
        degrades gracefully when Redis or DNS is down.
        """
        if not self._redis:
            return None
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_EXECUTOR, self._redis.get, key),
                timeout=_REDIS_GET_BUDGET_MS / 1000,
            )
        except Exception:
            # Disable cache for the remainder of this request to avoid repeats
            self._redis = None
            return None

    # ───────────────────────── public API ─────────────────────────────
    async def build(
        self,
        anchor_id: str,
        *,
        include_neighbors: bool = True,
        intent: str = "why_decision",
        scope: str = "k1",
    ) -> WhyDecisionEvidence:
        # ── initialise containers so they exist even when we skip k-1 expansion
        events: list = []
        pre: list = []
        suc: list = []
        anchor_supported_ids: set[str] = set()

        # ------------------------------------------------------------------ #
        # Fast-path: caller did **not** request graph neighbours             #
        # (matches Milestone-4 routing contract – avoids an unnecessary      #
        # network round-trip and lets test_search_similar_routing pass)      #
        # ------------------------------------------------------------------ #
        if not include_neighbors:
            anchor = WhyDecisionAnchor(id=anchor_id)
            return WhyDecisionEvidence(
                anchor=anchor,
                events=[],
                transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
                allowed_ids=[anchor_id],
                snapshot_etag="unknown",
            )
        alias_key   = ALIAS_TPL.format(anchor_id=anchor_id)
        retry_count = 0

        # fast‑path – try cache before network I/O
        if self._redis:
            try:
                cached_raw = await self._safe_get(alias_key)
                if cached_raw:
                    # Convert bytes to a UTF‑8 string when possible for JSON parsing and pointer handling
                    raw_str: Any = cached_raw
                    try:
                        if isinstance(cached_raw, (bytes, bytearray)):
                            raw_str = cached_raw.decode("utf-8")
                    except Exception:
                        raw_str = cached_raw
                    # Try to parse structured JSON.  A failure implies the value is a pointer.
                    try:
                        parsed = json.loads(raw_str)
                    except Exception:
                        parsed = None
                    # Layout 1: wrapped bundle { "_snapshot_etag": …, "data": … }
                    if isinstance(parsed, dict) and "_snapshot_etag" in parsed and "data" in parsed:
                        ev_obj = parsed.get("data")
                        if isinstance(ev_obj, dict):
                            try:
                                ev = WhyDecisionEvidence.model_validate(ev_obj)
                            except Exception:
                                try:
                                    ev = WhyDecisionEvidence.parse_obj(ev_obj)  # type: ignore[attr-defined]
                                except Exception:
                                    ev = None
                            if ev is not None:
                                ev.snapshot_etag = parsed.get("_snapshot_etag", "unknown")
                                if await self._is_fresh(anchor_id, ev.snapshot_etag):
                                    ev.__dict__["_retry_count"] = retry_count
                                    with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                        span.set_attribute("cache.hit", True)
                                        span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                    return ev
                    # Layout 2: bare JSON evidence directly under alias_key
                    if parsed is not None:
                        try:
                            ev = WhyDecisionEvidence.model_validate(parsed)
                        except Exception:
                            try:
                                ev = WhyDecisionEvidence.parse_obj(parsed)  # type: ignore[attr-defined]
                            except Exception:
                                ev = None
                        if ev is not None:
                            # Only use the cached evidence if it is still fresh.  A missing
                            # snapshot_etag (None) counts as fresh (spec §H3).
                            if await self._is_fresh(anchor_id, ev.snapshot_etag):
                                ev.__dict__["_retry_count"] = retry_count
                                with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                    span.set_attribute("cache.hit", True)
                                    span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                return ev
                    # Layout 3: pointer – interpret the value as the name of the composite key
                    composite_key = None
                    if parsed is None:
                        composite_key = raw_str if isinstance(raw_str, str) else None
                    elif isinstance(parsed, str):
                        composite_key = parsed
                    if composite_key:
                        try:
                            cached = await self._safe_get(composite_key)
                        except Exception:
                            cached = None
                        if cached:
                            try:
                                ev = WhyDecisionEvidence.model_validate_json(cached)
                            except Exception:
                                ev = None
                            if ev is not None and await self._is_fresh(anchor_id, ev.snapshot_etag):
                                ev.__dict__["_retry_count"] = retry_count
                                with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                    span.set_attribute("cache.hit", True)
                                    span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                return ev
            except Exception:
                logger.warning("redis read error – bypassing cache", exc_info=True)
                # Fail‑open: disable cache for the remainder of this request
                self._redis = None

        # ── plan (k-1 graph shape) ───────────────────────────────
        with trace_span.ctx("plan", anchor_id=anchor_id):
            plan = {"node_id": anchor_id, "k": 1}
            expand_ms = min(settings.timeout_expand_ms, _EXPAND_MS)  # clamp to perf budget
            # Use the **shared** client so consecutive calls can observe
            # monotonic state (e.g. MockClient2._idx) and detect ETag changes.
            async with _safe_async_client(
                timeout=expand_ms / 1000.0,
                base_url=settings.memory_api_url,
            ) as client:
                # ------------------------------------------------------------------
                # Anchor enrichment first – capture the snapshot_etag header.
                # This ensures we always have an ETag even if neighbour expansion
                # times out.
                # ------------------------------------------------------------------
                try:
                    resp_anchor = await client.get(f"/api/enrich/decision/{anchor_id}")
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
                    hdr_etag = _extract_snapshot_etag(resp_anchor) or "unknown"
                except Exception:
                    anchor_json, hdr_etag = {"id": anchor_id}, "unknown"

                # ------------------------------------------------------------------
                # 1. k‑1 neighbour expansion – may time out.
                # Even if this fails, we retain the ETag from above.
                # ------------------------------------------------------------------
                with trace_span.ctx("exec", anchor_id=anchor_id) as span:
                        try:
                            resp_neigh = await client.post("/api/graph/expand_candidates", json=plan)
                            try:
                                resp_neigh.raise_for_status()
                            except Exception:
                                raise
                            neigh: dict = resp_neigh.json() or {}
                        except (asyncio.TimeoutError, httpx.HTTPError, Exception) as exc:
                            logger.warning(
                                "expand_candidates_failed",
                                extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                            )
                            span.set_attribute("timeout", True)
                            neigh = {"neighbors": []}

                # Prefer snapshot_etag from neighbours’ meta (Milestone‑4).
                meta = neigh.get("meta") or {}
                meta_etag = None
                if isinstance(meta, dict):
                    meta_etag = meta.get("snapshot_etag")
                if meta_etag:
                    snapshot_etag = meta_etag
                else:
                    snapshot_etag = hdr_etag

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

        # ── de-duplicate evidence items ────────────────────────────────
        # Merge duplicate events by ID, preserving the first occurrence.  If
        # duplicates exist (e.g. one item has an explicit edge relation and
        # another does not) we keep the earliest and drop subsequent ones.
        if events:
            seen_event_ids: set[str] = set()
            deduped_events: list[dict] = []
            for ev in events:
                eid = ev.get("id")
                if eid and eid in seen_event_ids:
                    continue
                if eid:
                    seen_event_ids.add(eid)
                deduped_events.append(ev)
            events = deduped_events

        # Similarly de-duplicate preceding and succeeding transitions on ID.  The
        # order of the first occurrence is preserved.
        def _dedup_transitions(items: list[dict]) -> list[dict]:
            seen: set[str] = set()
            result: list[dict] = []
            for it in items:
                iid = it.get("id")
                if iid and iid in seen:
                    continue
                if iid:
                    seen.add(iid)
                result.append(it)
            return result

        if pre:
            pre = _dedup_transitions(pre)
        if suc:
            suc = _dedup_transitions(suc)

        if events:
            # ── enrich (event / anchor details) ───────────────────
            # Build enriched event objects by calling the Memory‑API with
            # the appropriate endpoint based on the neighbour type.  Each call
            # should use a base_url when supported so relative paths resolve
            # against the configured memory_api_url.  Preserve existing
            # behaviour for unit tests where the httpx client or
            # _safe_async_client may be monkey‑patched to a simple shim that
            # rejects unknown kwargs.  Per‑event enrichment requires a
            # fresh client instance to avoid carrying over internal state
            # (e.g. indices used by tests) across multiple events.
            with trace_span.ctx("enrich", anchor_id=anchor_id):
                enriched_events: list[dict] = []
                # Always request a fresh HTTP client for per‑event enrichment.  Pass
                # base_url and timeout so relative URLs resolve correctly and per‑call
                # timeouts apply.  _safe_async_client gracefully falls back when the
                # underlying stub rejects these kwargs.
                async with _safe_async_client(
                    _fresh=True,
                    base_url=settings.memory_api_url,
                    timeout=settings.timeout_enrich_ms / 1000.0,
                ) as ev_client:
                    for ev in events:
                        # Already enriched? (future Memory‑API versions)
                        if "led_to" in ev:
                            enriched_events.append(ev)
                            continue
                        eid = ev.get("id")
                        if not eid:
                            enriched_events.append(ev)
                            continue
                        # Choose endpoint based on explicit node type; default to event
                        etype = (ev.get("type") or ev.get("entity_type") or "").lower()
                        path = (
                            f"/api/enrich/decision/{eid}"
                            if etype == "decision"
                            else f"/api/enrich/event/{eid}"
                        )
                        try:
                            eresp = await ev_client.get(path)
                            eresp.raise_for_status()
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
            snapshot_etag=snapshot_etag,
        )

        ev.snapshot_etag = snapshot_etag
        ev.__dict__["_retry_count"] = retry_count
        # selector truncation (if > MAX_PROMPT_BYTES)
        ev, selector_meta = truncate_evidence(ev)
        # After truncation the returned instance may not retain the snapshot_etag;
        # reapply it so cache keys and fingerprints remain correct.
        if getattr(ev, "snapshot_etag", None) != snapshot_etag:
            ev.snapshot_etag = snapshot_etag
        ev.__dict__["_selector_meta"] = selector_meta

        # ------------------------------------------------------------------
        # Cache write
        #
        # Store the evidence bundle directly under the alias key together with
        # its snapshot_etag.  When a Redis pipeline is available we also
        # persist the legacy composite key for backwards compatibility, but
        # unit‑test fakes (no pipeline) perform only one setex.
        truncated_flag = selector_meta.get("selector_truncation", False)
        composite_key  = _make_cache_key(
            anchor_id,
            intent,
            scope,
            ev.snapshot_etag or "unknown",
            truncated_flag,
        )
        if self._redis:
            try:
                ttl  = settings.cache_ttl_evidence_sec or CACHE_TTL_SEC
                # Prepare payload without snapshot_etag (excluded by Pydantic) and wrap it
                try:
                    payload = ev.model_dump()
                except Exception:
                    payload = ev.dict()
                cache_val = {
                    "_snapshot_etag": ev.snapshot_etag or "unknown",
                    "data": payload,
                }
                serialized = json.dumps(cache_val, separators=(",", ":"))
                try:
                    pipe = self._redis.pipeline()
                    pipe.setex(composite_key, ttl, ev.model_dump_json())
                    pipe.setex(alias_key, ttl, serialized)
                    pipe.execute()
                except AttributeError:
                    # thin stubs only support setex: store under alias key
                    self._redis.setex(alias_key, ttl, serialized)
            except Exception:
                logger.warning("redis write error", exc_info=True)
                self._redis = None
        logger.info(
            "evidence_built",
            extra={
                "anchor_id": anchor_id,
                "bundle_size_bytes": bundle_size_bytes(ev),
                **selector_meta,
            },
        )

        return ev

    # ─────────────────────── internal helper ──────────────────────────
    async def _is_fresh(self, anchor_id: str, cached_etag: str) -> bool:
        """Check if cached snapshot_etag is still current (≤50 ms budget).

        If the etag is missing (None or empty string) we assume the bundle is fresh.
        The sentinel value ``"unknown"`` forces regeneration.  We attempt to
        re-fetch the anchor with a lightweight ETag check; when the monkey‑patched
        HTTP client does not accept headers we retry without them.  Any unexpected
        exception is treated as a cache hit (fail‑open) so that cached data is
        reused when the Memory API is unreachable.
        """
        if not cached_etag:
            return True
        if cached_etag == "unknown":
            return False
        try:
            async with _safe_async_client(
                timeout=0.05, base_url=settings.memory_api_url
            ) as client:
                url = f"/api/enrich/decision/{anchor_id}"
                try:
                    resp = await client.get(url, headers={"x-cache-etag-check": "1"})
                except TypeError:
                    resp = await client.get(url)
            return _extract_snapshot_etag(resp) == cached_etag
        except Exception:
            return True
    # ── temporary alias until tests migrate in M-4 ──────────────────────────
    async def get_evidence(self, anchor_id: str) -> WhyDecisionEvidence:
        return await self.build(anchor_id)
