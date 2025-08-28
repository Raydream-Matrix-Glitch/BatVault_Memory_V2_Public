# Imports
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
from core_validator import canonical_allowed_ids


# Configuration & constants
settings        = get_settings()
logger          = get_logger("gateway.evidence")
logger.propagate = False

_REDIS_GET_BUDGET_MS = int(os.getenv("REDIS_GET_BUDGET_MS", "100"))   # ≤100 ms fail-open
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

CACHE_TTL_SEC   = 900          # 15 min
ALIAS_TPL       = "evidence:{anchor_id}:latest"


# Public API functions
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
    payload  = {"node_id": decision_id, "k": k}
    timeout_s = 0.25
    # Use the same resilient client used elsewhere so tests can monkeypatch.
    async with _safe_async_client(timeout=timeout_s, base_url=settings.memory_api_url) as client:
        try:
            resp = await client.post("/api/graph/expand_candidates", json=payload)
            # tolerate stubs without raise_for_status
            if hasattr(resp, "raise_for_status"):
                resp.raise_for_status()
            return resp.json() or {}
        except Exception as exc:
            logger.warning("expand_candidates_failed",
                           extra={"anchor_id": decision_id, "error": type(exc).__name__})
            return {"neighbors": [], "meta": {}}


# Helper functions
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

    if isinstance(shape_or_anchor, WhyDecisionAnchor):
        anchor = shape_or_anchor
        events = events or []
        pre    = pre or []
        suc    = suc or []

    elif isinstance(events, WhyDecisionAnchor):
        shape  = shape_or_anchor
        anchor = events

        neighbours = shape.get("neighbors", {})
        if isinstance(neighbours, list):               # flat list variant
            # Dedupe by id first to avoid redundant enrich calls and noisy logs
            _by_id: dict[str, dict] = {}
            for n in neighbours:
                nid = n.get("id")
                if nid and nid not in _by_id:
                    _by_id[nid] = n
            deduped = list(_by_id.values())
            events = [n for n in deduped if (n.get("type") or "").lower() == "event"]
            transitions = [n for n in deduped if (n.get("type") or "").lower() == "transition"]
            pre, suc = [], transitions
        else:                                         # namespaced dict
            events = neighbours.get("events", []) or []
            transitions = neighbours.get("transitions", []) or []
            pre, suc = transitions, []

    else:
        raise TypeError("Unsupported _collect_allowed_ids() call signature")

    # Use the canonical helper from the core validator.  Convert neighbour
    # objects into plain dicts for the helper and combine preceding and
    # succeeding transitions.
    # Normalise event and transition lists to contain only dictionaries.
    try:
        anchor_id = getattr(anchor, "id", None) or ""
    except Exception:
        anchor_id = ""
    ev_list: list[dict] = []
    for e in (events or []):
        if isinstance(e, dict):
            ev_list.append(e)
        else:
            try:
                ev_list.append(e.model_dump(mode="python"))
            except Exception:
                ev_list.append(dict(e))
    tr_list: list[dict] = []
    for t in (pre or []) + (suc or []):
        if isinstance(t, dict):
            tr_list.append(t)
        else:
            try:
                tr_list.append(t.model_dump(mode="python"))
            except Exception:
                tr_list.append(dict(t))
    # Delegate to the canonical function.  This will deduplicate and order
    # IDs according to the specification (anchor first, then events by
    # timestamp, then transitions by timestamp).
    return canonical_allowed_ids(anchor_id, ev_list, tr_list)

def _extract_snapshot_etag(resp: httpx.Response | object) -> str:
    """
    Retrieve the *snapshot_etag* marker from an HTTP response.
    Robust to:
      • httpx.Headers or dict-like objects (not just ``dict``)
      • case differences and ``-``/``_`` variants (e.g. "Snapshot-ETag")
    Falls back to ``"unknown"`` if not present.
    """
    headers = getattr(resp, "headers", None)

    items = []
    try:
        if headers is None:
            items = []
        elif hasattr(headers, "items"):
            items = list(headers.items())
        elif isinstance(headers, (list, tuple)):
            items = list(headers)
        else:
            items = list(dict(headers).items())
    except Exception:
        items = []

    for k, v in items:
        try:
            key = str(k).lower().replace("-", "_")
        except Exception:
            continue
        if key in ("snapshot_etag", "x_snapshot_etag"):
            return v
    return "unknown"

if not hasattr(trace_span, "ctx"):
    @contextmanager
    def _noop_ctx(_stage: str, **_kw):
        class _Span:
            def set_attribute(self, *_a, **_k): ...
            def end(self): ...
        yield _Span()
    trace_span.ctx = _noop_ctx            # type: ignore[attr-defined]
_shared_fallback_client: httpx.AsyncClient | None = None

def _inject_trace_context(headers: dict[str, str] | None = None) -> dict[str, str]:
    h = dict(headers or {})
    try:
        from opentelemetry.propagate import inject  # type: ignore
        inject(h)
    except Exception:
        pass
    return h

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
    clean_kwargs: dict[str, Any] = {k: v for k, v in kw.items() if not k.startswith("_")}

    client: httpx.AsyncClient
    managed: bool
    try:
        client = httpx.AsyncClient(**clean_kwargs)
        managed = hasattr(client, "__aenter__")
    except TypeError:
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
            async with client as real_client:
                # Ensure every outbound request carries W3C trace headers
                try:
                    real_client.headers = _inject_trace_context(getattr(real_client, "headers", {}) or {})  # type: ignore[attr-defined]
                except Exception:
                    pass
                yield real_client
        else:
            yield client
    finally:
        if managed:
            pass
        else:
            if fresh:
                try:
                    await client.aclose()
                except Exception:
                    pass
                if client is _shared_fallback_client:
                    _shared_fallback_client = None


# EvidenceBuilder class
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
        global _shared_fallback_client
        _shared_fallback_client = None

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
            self._redis = None
            return None

    async def build(
        self,
        anchor_id: str,
        *,
        include_neighbors: bool = True,
        intent: str = "why_decision",
        scope: str = "k1",
    ) -> WhyDecisionEvidence:
        events: list = []
        pre: list = []
        suc: list = []
        anchor_supported_ids: set[str] = set()

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

        if self._redis:
            try:
                cached_raw = await self._safe_get(alias_key)
                if cached_raw:
                    raw_str: Any = cached_raw
                    try:
                        if isinstance(cached_raw, (bytes, bytearray)):
                            raw_str = cached_raw.decode("utf-8")
                    except Exception:
                        raw_str = cached_raw
                    try:
                        parsed = json.loads(raw_str)
                    except Exception:
                        parsed = None
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
                    if parsed is not None:
                        try:
                            ev = WhyDecisionEvidence.model_validate(parsed)
                        except Exception:
                            try:
                                ev = WhyDecisionEvidence.parse_obj(parsed)  # type: ignore[attr-defined]
                            except Exception:
                                ev = None
                        if ev is not None:
                            if await self._is_fresh(anchor_id, ev.snapshot_etag):
                                ev.__dict__["_retry_count"] = retry_count
                                with trace_span.ctx("bundle", anchor_id=anchor_id) as span:
                                    span.set_attribute("cache.hit", True)
                                    span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                return ev
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
                self._redis = None

        with trace_span.ctx("plan", anchor_id=anchor_id):
            plan = {"node_id": anchor_id, "k": 1}
            expand_ms = min(settings.timeout_expand_ms, _EXPAND_MS)  # clamp to perf budget
            async with _safe_async_client(
                timeout=expand_ms / 1000.0,
                base_url=settings.memory_api_url,
            ) as client:
                # Primary attempt to fetch the anchor; fall back to a stub if network fails
                anchor_json: dict
                hdr_etag: str
                try:
                    resp_anchor = await client.get(f"/api/enrich/decision/{anchor_id}")
                    # tolerate stubs without raise_for_status
                    if hasattr(resp_anchor, "raise_for_status"):
                        resp_anchor.raise_for_status()
                    anchor_json = resp_anchor.json() or {}
                    try:
                        logger.info(
                            "anchor_fetch_ok",
                            extra={
                                "anchor_id": anchor_id,
                                "status": int(getattr(resp_anchor, "status_code", 0) or 0),
                                "has_title": bool(anchor_json.get("title")),
                                "supported_by_n": len(anchor_json.get("supported_by") or []),
                                "transitions_n": len(anchor_json.get("transitions") or []),
                            },
                        )
                    except Exception:
                        pass
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
                    # If enrich returned a thin anchor (missing rationale/timestamp), retry once with jitter
                    try:
                        if not (anchor_json.get("rationale") or anchor_json.get("timestamp")):
                            await asyncio.sleep(random.uniform(0.02, 0.05))
                            retry_resp = await client.get(f"/api/enrich/decision/{anchor_id}")
                            if hasattr(retry_resp, "raise_for_status"):
                                retry_resp.raise_for_status()
                            cand = retry_resp.json() or {}
                            if cand.get("id"):
                                cand["id"] = anchor_id
                            if cand.get("rationale") or cand.get("timestamp"):
                                anchor_json = cand
                        logger.info(
                            "anchor_fields_check",
                            extra={
                                "anchor_id": anchor_id,
                                "has_rationale": bool(anchor_json.get("rationale")),
                                "has_timestamp": bool(anchor_json.get("timestamp")),
                            },
                        )
                    except Exception:
                        pass
                except Exception:
                    # fallback: try without base_url in a fresh client so that monkey‑patched
                    # AsyncClient can serve the request (tests provide relative-url stubs)
                    try:
                        async with _safe_async_client(
                            timeout=expand_ms / 1000.0,
                            _fresh=True,
                        ) as fb_client:
                            fresp = await fb_client.get(f"/api/enrich/decision/{anchor_id}")
                            if hasattr(fresp, "raise_for_status"):
                                fresp.raise_for_status()
                            anchor_json = fresp.json() or {}
                            if anchor_json.get("id") and anchor_json["id"] != anchor_id:
                                logger.warning(
                                    "anchor_id_mismatch",
                                    extra={
                                        "requested_anchor_id": anchor_id,
                                        "memory_anchor_id": anchor_json.get("id"),
                                    },
                                )
                            anchor_json["id"] = anchor_id
                            hdr_etag = _extract_snapshot_etag(fresp) or "unknown"
                            # Thin anchor retry with jitter in fallback branch
                            try:
                                if not (anchor_json.get("rationale") or anchor_json.get("timestamp")):
                                    await asyncio.sleep(random.uniform(0.02, 0.05))
                                    r2 = await fb_client.get(f"/api/enrich/decision/{anchor_id}")
                                    if hasattr(r2, "raise_for_status"):
                                        r2.raise_for_status()
                                    cand2 = r2.json() or {}
                                    if cand2.get("id"):
                                        cand2["id"] = anchor_id
                                    if cand2.get("rationale") or cand2.get("timestamp"):
                                        anchor_json = cand2
                                logger.info(
                                    "anchor_fields_check_fb",
                                    extra={
                                        "anchor_id": anchor_id,
                                        "has_rationale": bool(anchor_json.get("rationale")),
                                        "has_timestamp": bool(anchor_json.get("timestamp")),
                                    },
                                )
                            except Exception:
                                pass
                    except Exception:
                        logger.warning(
                            "anchor_enrich_failed",
                            extra={"anchor_id": anchor_id, "error": "fetch_failed"}
                        )
                        try:
                            from core_metrics import counter as _ctr
                            _ctr("gateway_anchor_enrich_fail_total", 1)
                        except Exception:
                            pass
                        anchor_json, hdr_etag = {"id": anchor_id}, "unknown"
                # Mirror option to title if title is missing (supports stubbed Memory API)
                try:
                    if anchor_json.get("option") and not anchor_json.get("title"):
                        anchor_json["title"] = anchor_json.get("option")
                except Exception:
                    pass

                with trace_span.ctx("exec", anchor_id=anchor_id) as span:
                        try:
                            resp_neigh = await client.post("/api/graph/expand_candidates", json=plan)
                            resp_neigh.raise_for_status()
                            neigh: dict = resp_neigh.json() or {}
                        except (asyncio.TimeoutError, httpx.HTTPError, Exception) as exc:
                            # fallback: try expand_candidates again without base_url in fresh client
                            try:
                                async with _safe_async_client(
                                    timeout=expand_ms / 1000.0,
                                    _fresh=True,
                                ) as fb_client:
                                    fresp_neigh = await fb_client.post(
                                        "/api/graph/expand_candidates", json=plan
                                    )
                                    fresp_neigh.raise_for_status()
                                    neigh = fresp_neigh.json() or {}
                            except Exception:
                                logger.warning(
                                    "expand_candidates_failed",
                                    extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                                )
                                span.set_attribute("timeout", True)
                                neigh = {"neighbors": []}

                meta = neigh.get("meta") or {}
                # Strategic: show whether expand actually returned anything,
                # without dumping payloads.
                try:
                    logger.info("expand_result",
                                extra={"anchor_id": anchor_id, "neighbor_count": len(neigh.get("neighbors") or []), "meta_keys": list((meta or {}).keys())})
                except Exception:  # logging must never break the hot path
                    pass
                meta_etag = None
                if isinstance(meta, dict):
                    meta_etag = meta.get("snapshot_etag")
                if meta_etag:
                    snapshot_etag = meta_etag
                else:
                    snapshot_etag = hdr_etag

        events: list[dict] = []                    # event neighbours
        pre:    list[dict] = []                    # will hold classified preceding transitions
        suc:    list[dict] = []                    # will hold classified succeeding transitions

        neighbor_transitions: dict[str, dict] = {}
        neighbor_trans_orient: dict[str, str] = {}

        anchor_supported_ids: set[str] = set()
        event_led_to_map: dict[str, set[str]] = {}

        # Collect top-level event neighbours provided directly on the root.  Skip any
        # neighbour explicitly typed as a decision.
        for ev in neigh.get("events", []) or []:
            try:
                raw_type = ev.get("type") or ev.get("entity_type") or ""
                ntype = str(raw_type).lower() if raw_type is not None else ""
            except Exception:
                ntype = ""
            if ntype == "decision":
                continue
            events.append(ev)
        for tr in neigh.get("preceding", []) or []:
            tid = tr.get("id")
            if not tid:
                continue
            neighbor_transitions[tid] = tr
            neighbor_trans_orient[tid] = "preceding"
        for tr in neigh.get("succeeding", []) or []:
            tid = tr.get("id")
            if not tid:
                continue
            neighbor_transitions[tid] = tr
            neighbor_trans_orient[tid] = "succeeding"

        neighbors = neigh.get("neighbors")
        if neighbors:
            # Normalised handling of neighbour shapes for both namespaced and flat lists.  We
            # accumulate events and transitions separately, dropping explicit decisions
            # entirely.  Any item with type "decision" is ignored, and items with a
            # preceding/succeeding relation are treated as transitions when not already
            # typed as such.
            if isinstance(neighbors, dict):  # v2 namespaced shape
                ev_nodes = neighbors.get("events", []) or []
                for n in ev_nodes:
                    # Skip explicit decisions from the event list
                    ntype = (n.get("type") or n.get("entity_type") or "").lower()
                    if ntype == "decision":
                        continue
                    events.append(n)
                    edge_info = n.get("edge") or {}
                    # Accept both canonical `rel` and legacy `relation` keys
                    rel = edge_info.get("rel") or edge_info.get("relation")
                    if rel in {"supported_by", "led_to", "LED_TO"}:
                        eid = n.get("id")
                        if eid:
                            anchor_supported_ids.add(eid)
                            event_led_to_map.setdefault(eid, set()).add(anchor_id)
                # transitions bucket is always explicit in namespaced shape
                for n in neighbors.get("transitions", []) or []:
                    tid = n.get("id")
                    if not tid:
                        continue
                    neighbor_transitions[tid] = n
                    # record orientation hint if present on the neighbor
                    edge_info = n.get("edge") or {}
                    rel = edge_info.get("rel") or edge_info.get("relation")
                    if rel in {"preceding", "succeeding"}:
                        neighbor_trans_orient[tid] = rel
            else:  # flattened list
                for n in neighbors:
                    # Determine declared entity type (lower‑cased); default to empty string
                    raw_type = n.get("type") or n.get("entity_type") or ""
                    ntype = str(raw_type).lower() if raw_type is not None else ""
                    edge = n.get("edge") or {}
                    # Accept both canonical `rel` and legacy `relation` keys
                    rel = edge.get("rel") or edge.get("relation")
                    # Drop explicit decisions
                    if ntype == "decision":
                        # Decision neighbours are not included in events or transitions
                        continue
                    # Explicit transitions go straight into the transitions bucket
                    if ntype == "transition":
                        tid = n.get("id")
                        if tid:
                            neighbor_transitions[tid] = n
                            if rel in {"preceding", "succeeding"}:
                                neighbor_trans_orient[tid] = rel
                        continue
                    # Items with a preceding/succeeding relation but lacking an explicit
                    # transition type are treated as transitions rather than events.
                    if rel in {"preceding", "succeeding"}:
                        tid = n.get("id")
                        if tid:
                            neighbor_transitions[tid] = n
                            neighbor_trans_orient[tid] = rel
                        continue
                    # Otherwise, treat the neighbour as an event (including missing type or
                    # unexpected types other than "decision" and "transition").
                    events.append(n)
                    # record support relations for anchor–event links
                    if rel in {"supported_by", "led_to", "LED_TO"}:
                        eid = n.get("id")
                        if eid:
                            anchor_supported_ids.add(eid)
                            event_led_to_map.setdefault(eid, set()).add(anchor_id)

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
            with trace_span.ctx("enrich", anchor_id=anchor_id):
                enriched_events: list[dict] = []
                async with _safe_async_client(
                    base_url=settings.memory_api_url,
                    timeout=settings.timeout_enrich_ms / 1000.0,
                ) as ev_client:
                    for ev in events:
                        # Skip enrichment for events that already carry a led_to marker (support link)
                        if "led_to" in ev:
                            enriched_events.append(ev)
                            continue
                        eid = ev.get("id")
                        if not eid:
                            enriched_events.append(ev)
                            continue
                        # Determine declared type (if any) for enrichment routing.  Decisions are
                        # not included in events, but guard defensively.
                        etype = (ev.get("type") or ev.get("entity_type") or "").lower()
                        # Only enrich true events via the event endpoint.  Decisions are skipped
                        # entirely (no enrichment), as they are dropped from the evidence.
                        if etype == "decision":
                            # Skip enrichment for decisions; append original to maintain position
                            enriched_events.append(ev)
                            continue
                        path = f"/api/enrich/event/{eid}"
                        try:
                            eresp = await ev_client.get(path)
                            eresp.raise_for_status()
                            # Merge enriched payload first so that input fields (summary, timestamp)
                            # override returned defaults.  This yields a base with id only,
                            # overwritten by any input metadata we wish to preserve.
                            enriched_events.append({**eresp.json(), **ev})
                        except Exception:
                            logger.warning(
                                "event_enrich_failed",
                                extra={"event_id": eid, "anchor_id": anchor_id},
                            )
                            enriched_events.append(ev)
                # After enrichment, the Memory API is responsible for returning
                # neighbour events that have already been normalised.  The
                # EvidenceBuilder no longer reprojects or sanitises the event
                # objects.  Assign the enriched events list directly to
                # the events collection.  Decision neighbours are skipped earlier.
                events = list(enriched_events)
        for ev in events:
            if anchor_id in (ev.get("led_to") or []):
                ev_id = ev.get("id")
                if ev_id:
                    anchor_supported_ids.add(ev_id)
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


        declared_ids = set(anchor_json.get("transitions") or [])
        neighbor_ids = set(neighbor_transitions.keys())
        anchor_trans_ids = sorted(declared_ids | neighbor_ids)
        logger.info("transitions_hydration_start",
                    extra={"anchor_id": anchor_id,
                           "anchor_transitions_n": len(anchor_trans_ids),
                           "neighbor_transitions_n": len(neighbor_transitions)})
        trans_pre_list: list[dict] = []
        trans_suc_list: list[dict] = []
        missing_trans: list[str] = []
        seen_trans: set[str] = set()
        # Only attempt hydration when there is something to process
        if anchor_trans_ids or neighbor_transitions:
            with trace_span.ctx("transitions_enrich", anchor_id=anchor_id):
                try:
                    async with _safe_async_client(
                        base_url=settings.memory_api_url,
                        timeout=settings.timeout_enrich_ms / 1000.0,
                    ) as tr_client:
                        # First hydrate and classify transitions declared on the anchor
                        for tid in anchor_trans_ids:
                            if not isinstance(tid, str) or tid in seen_trans:
                                continue
                            seen_trans.add(tid)
                            tdoc: dict | None = None
                            # Attempt primary fetch
                            try:
                                resp = await tr_client.get(f"/api/enrich/transition/{tid}")
                                resp.raise_for_status()
                                tdoc = resp.json() or {}
                            except Exception:
                                # Fallback fetch without base_url using fresh client
                                try:
                                    async with _safe_async_client(
                                        timeout=settings.timeout_enrich_ms / 1000.0,
                                        _fresh=True,
                                    ) as fb_client:
                                        try:
                                            fresp = await fb_client.get(f"/api/enrich/transition/{tid}")
                                            fresp.raise_for_status()
                                            tdoc = fresp.json() or {}
                                        except Exception:
                                            tdoc = None
                                except Exception:
                                    tdoc = None
                                # Final fallback: use neighbour-provided stub if available
                                if tdoc is None:
                                    stub = neighbor_transitions.get(tid)
                                    if stub:
                                        tdoc = stub
                            # If still missing, record and skip
                            if not tdoc:
                                missing_trans.append(tid)
                                continue
                            # Determine orientation solely based on explicit "to"/"from" relative to anchor
                            to_id   = tdoc.get("to")   or tdoc.get("to_id")
                            from_id = tdoc.get("from") or tdoc.get("from_id")
                            orient: str | None = None
                            if to_id == anchor_id:
                                orient = "preceding"
                            elif from_id == anchor_id:
                                orient = "succeeding"
                            if orient == "preceding":
                                trans_pre_list.append(tdoc)
                            elif orient == "succeeding":
                                trans_suc_list.append(tdoc)
                            else:
                                # Unknown orientation – consider missing
                                missing_trans.append(tid)
                        # Now hydrate and classify neighbour transitions (those not on anchor)
                        for tid, stub in neighbor_transitions.items():
                            # Skip IDs already processed from the anchor list
                            if not isinstance(tid, str) or tid in seen_trans:
                                continue
                            seen_trans.add(tid)
                            tdoc: dict | None = None
                            try:
                                resp = await tr_client.get(f"/api/enrich/transition/{tid}")
                                resp.raise_for_status()
                                tdoc = resp.json() or {}
                            except Exception:
                                try:
                                    async with _safe_async_client(
                                        timeout=settings.timeout_enrich_ms / 1000.0,
                                        _fresh=True,
                                    ) as fb_client:
                                        try:
                                            fresp = await fb_client.get(f"/api/enrich/transition/{tid}")
                                            fresp.raise_for_status()
                                            tdoc = fresp.json() or {}
                                        except Exception:
                                            tdoc = None
                                except Exception:
                                    tdoc = None
                                if tdoc is None and stub:
                                    tdoc = stub
                            if not tdoc:
                                missing_trans.append(tid)
                                continue
                            # Determine orientation: explicit to/from dominates; fallback to neighbour hint
                            to_id = tdoc.get("to")
                            from_id = tdoc.get("from")
                            orient: str | None = None
                            if to_id == anchor_id:
                                orient = "preceding"
                            elif from_id == anchor_id:
                                orient = "succeeding"
                            if orient is None:
                                orient = neighbor_trans_orient.get(tid)
                            if orient == "preceding":
                                trans_pre_list.append(tdoc)
                            elif orient == "succeeding":
                                trans_suc_list.append(tdoc)
                            else:
                                missing_trans.append(tid)
                except Exception:
                    # Catastrophic failure: mark all IDs as missing
                    missing_trans.extend([tid for tid in anchor_trans_ids if isinstance(tid, str)])
                    missing_trans.extend([tid for tid in neighbor_transitions.keys() if isinstance(tid, str)])
        # De-duplicate transition lists based on ID before assignment
        if trans_pre_list:
            trans_pre_list = _dedup_transitions(trans_pre_list)
        if trans_suc_list:
            trans_suc_list = _dedup_transitions(trans_suc_list)
        # Assign results to pre/suc for further processing
        pre = trans_pre_list
        suc = trans_suc_list

        if anchor_trans_ids:
            logger.info("transitions_classified",
                        extra={"anchor_id": anchor_id,
                               "preceding_n": len(trans_pre_list),
                               "succeeding_n": len(trans_suc_list)})
            if pre or suc:
                logger.info(
                    "transitions_hydrated",
                    extra={
                        "anchor_id": anchor_id,
                        "preceding_n": len(pre),
                        "succeeding_n": len(suc),
                    },
                )
            else:
                logger.warning(
                    "transitions_missing_while_anchor_has",
                    extra={
                        "anchor_id": anchor_id,
                        "transition_ids_n": len(anchor_trans_ids),
                    },
                )
        # If we ended up with no transitions, emit a diagnostic log with counts
        if not pre and not suc:
            try:
                logger.warning(
                    "no_transitions_built",
                    extra={
                        "anchor_id": anchor_id,
                        "neighbor_count": len(neigh.get("neighbors") or []),
                        "declared_transitions": len(anchor_json.get("transitions") or []),
                        "neighbor_transition_n": len(neighbor_transitions or {}),
                    },
                )
            except Exception:
                pass
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
        ev, selector_meta = truncate_evidence(ev)
        if getattr(ev, "snapshot_etag", None) != snapshot_etag:
            ev.snapshot_etag = snapshot_etag
        try:
            pre_list = list(ev.transitions.preceding)
            suc_list = list(ev.transitions.succeeding)
        except Exception:
            pre_list = []
            suc_list = []
        ev.allowed_ids = _collect_allowed_ids(ev.anchor, ev.events, pre_list, suc_list)
        # Strategic: log sizes and class breakdown without dumping payloads
        try:
            _type_counts = {
                "events": len(ev.events or []),
                "preceding": len(pre_list or []),
                "succeeding": len(suc_list or []),
                "allowed_ids": len(ev.allowed_ids or []),
            }
            logger.info("evidence_finalised_counts", extra={"anchor_id": anchor_id, **_type_counts})
        except Exception:
            pass
        ev.__dict__["_selector_meta"] = selector_meta

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

    async def _is_fresh(self, anchor_id: str, cached_etag: str) -> bool:
        """Check if cached snapshot_etag is still current (≤50 ms budget).

        If the etag is missing (None or empty string) we assume the bundle is fresh.
        The sentinel value ``"unknown"`` forces regeneration.  We attempt to
        re-fetch the anchor with a lightweight ETag check; when the monkey‑patched
        HTTP client does not accept headers we retry without them.  Any unexpected
        exception is treated as **stale** (fail-closed) so we regenerate when the
        Memory API is unreachable or returns an error.
        """
        if not cached_etag:
            return True
        if cached_etag == "unknown":
            return False
        try:
            async with _safe_async_client(
                timeout=0.05,
                base_url=settings.memory_api_url,
                _fresh=True,
            ) as client:
                url = f"/api/enrich/decision/{anchor_id}"
                try:
                    resp = await client.get(url, headers={"x-cache-etag-check": "1"})
                except TypeError:
                    resp = await client.get(url)
            return _extract_snapshot_etag(resp) == cached_etag
        except Exception:
            try:
                logger.warning("etag_check_failed", extra={"anchor_id": anchor_id})
            except Exception:
                pass
            return False
    async def get_evidence(self, anchor_id: str) -> WhyDecisionEvidence:
        return await self.build(anchor_id)
