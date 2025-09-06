# Imports
from __future__ import annotations

import asyncio, hashlib, random
import re

from typing import Any, Optional

from .metrics import counter as _ctr
from gateway.redis import get_redis_pool
from core_utils import jsonx
from core_config import get_settings
from core_logging import get_logger, trace_span
from core_http.client import fetch_json, get_http_client
from core_observability.otel import inject_trace_context
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionTransitions,
)
from .logging_helpers import stage as log_stage
from .budget_gate import authoritative_truncate
from .selector import bundle_size_bytes
from core_validator import canonical_allowed_ids
from shared.normalize import normalise_event_amount as _normalise_event_amount


# Configuration & constants
settings        = get_settings()
logger          = get_logger("gateway.evidence")
logger.propagate = False

_REDIS_GET_BUDGET_MS = int(get_settings().redis_get_budget_ms)        # ≤100 ms fail-open

CACHE_TTL_SEC   = 900          # 15 min
ALIAS_TPL       = "evidence:{anchor_id}:latest"

# Public API functions
__all__ = [
    "resolve_anchor",
    "expand_graph",
    "WhyDecisionEvidence",
    "_collect_allowed_ids",
]

@trace_span("resolve", logger=logger)
async def resolve_anchor(decision_ref: str, *, intent: str | None = None):
    await asyncio.sleep(0)
    return {"id": decision_ref}

async def expand_graph(decision_id: str, *, intent: str | None = None, k: int = 1, policy_headers: dict | None = None):
    settings = get_settings()
    payload = {"node_id": decision_id, "k": k}
    try:
        data = await fetch_json(
            "POST",
            f"{settings.memory_api_url}/api/graph/expand_candidates",
            json=payload,
            stage="expand",
            headers=inject_trace_context(dict(policy_headers or {})),
        )
        return data or {"neighbors": [], "meta": {}}
    except Exception as exc:
        logger.warning("expand_candidates_failed", extra={"anchor_id": decision_id, "error": type(exc).__name__})
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
    return canonical_allowed_ids(anchor_id, ev_list, tr_list)

def _extract_snapshot_etag(resp: Any) -> str:
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

# EvidenceBuilder class
class EvidenceBuilder:
    def __init__(self, *, redis_client: Optional[Any] = None):
        if redis_client is not None:
            self._redis = redis_client
        else:
            try:
                # Use the shared async Redis pool by default; this will
                # return a redis.asyncio.Redis instance.  Fall back to
                # ``None`` on failure to ensure cache is bypassed.
                self._redis = get_redis_pool()
            except Exception:
                self._redis = None

    async def _safe_get(self, key: str):
        if not self._redis:
            return None
        try:
            return await asyncio.wait_for(self._redis.get(key), timeout=_REDIS_GET_BUDGET_MS / 1000)
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
        fresh: bool = False,
        policy_headers: dict | None = None,
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
        stale_ev: WhyDecisionEvidence | None = None

        if self._redis and not fresh:
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
                        parsed = jsonx.loads(raw_str)
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
                                    with trace_span("bundle", anchor_id=anchor_id) as span:
                                        span.set_attribute("cache.hit", True)
                                        span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                    return ev
                                else:
                                    # keep not-fresh bundle as stale candidate in case enrich times out
                                    stale_ev = ev
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
                                with trace_span("bundle", anchor_id=anchor_id) as span:
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
                                with trace_span("bundle", anchor_id=anchor_id) as span:
                                    span.set_attribute("cache.hit", True)
                                    span.set_attribute("bundle_size_bytes", bundle_size_bytes(ev))
                                return ev
            except Exception:
                logger.warning("redis read error – bypassing cache", exc_info=True)
                self._redis = None
        elif fresh:
            try:
                log_stage("cache", "bypass", anchor_id=anchor_id, reason="fresh=true")
            except Exception:
                # keep hot path resilient
                pass

        with trace_span("plan", anchor_id=anchor_id):
            plan = {"node_id": anchor_id, "k": 1}
            # Use configured per-stage budgets; defaults already come from constants/env.
            enrich_ms = int(settings.timeout_enrich_ms)
            expand_ms = int(settings.timeout_expand_ms)
            enrich_client = get_http_client(timeout_ms=int(enrich_ms))
            expand_client = get_http_client(timeout_ms=int(expand_ms))
            # Concurrently fetch anchor and neighbors; each task handles its own errors
            anchor_json: dict = {"id": anchor_id}
            hdr_etag: str = "unknown"
            neigh: dict = {"neighbors": []}
            meta: dict | None = None
            policy_trace: dict | None = None

            def _has_meaningful_anchor(d: dict) -> bool:
                return bool(d.get("title") or d.get("option") or d.get("rationale") or d.get("timestamp") or d.get("decision_maker"))

            async def _fetch_anchor():
                nonlocal anchor_json, hdr_etag
                try:
                    resp_anchor = await enrich_client.get(
                        f"{settings.memory_api_url}/api/enrich/decision/{anchor_id}",
                        headers=inject_trace_context(dict(policy_headers or {})),
                    )
                    if hasattr(resp_anchor, "raise_for_status"):
                        resp_anchor.raise_for_status()
                    try:
                        anchor_json = jsonx.loads(resp_anchor.content)
                    except Exception:
                        anchor_json = resp_anchor.json() or {"id": anchor_id}
                    hdr_etag = _extract_snapshot_etag(resp_anchor) or "unknown"
                    try:
                        logger.info(
                            "anchor_enrich_ok",
                            extra={
                                "anchor_id": anchor_id,
                                "has_rationale": bool(anchor_json.get("rationale")),
                                "has_timestamp": bool(anchor_json.get("timestamp")),
                                "has_decision_maker": bool(anchor_json.get("decision_maker")),
                            },
                        )
                    except Exception:
                        pass
                except Exception as exc:
                    try:
                        logger.warning(
                            "anchor_enrich_failed",
                            extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                        )
                        _ctr("gateway_anchor_enrich_fail_total", 1)
                    except Exception:
                        pass
                    # If upstream replied with 504, surface a distinct signal for dashboards
                    try:
                        _status = getattr(getattr(exc, "response", None), "status_code", None)
                        if _status == 504:
                            logger.warning(
                                "anchor_enrich_upstream_timeout",
                                extra={"anchor_id": anchor_id, "timeout_ms": int(enrich_ms)}
                            )
                    except Exception:
                        pass
                    # Jittered single retry ONLY if we don't yet have a meaningful anchor.
                    if not _has_meaningful_anchor(anchor_json):
                        try:
                            await asyncio.sleep(random.uniform(0.02, 0.05))
                            retry_resp = await enrich_client.get(
                                f"{settings.memory_api_url}/api/enrich/decision/{anchor_id}",
                                headers=inject_trace_context(dict(policy_headers or {})),
                            )
                            if hasattr(retry_resp, "raise_for_status"):
                                retry_resp.raise_for_status()
                            try:
                                cand = jsonx.loads(retry_resp.content)
                            except Exception:
                                cand = retry_resp.json() or {}
                            if cand.get("id"):
                                cand["id"] = anchor_id
                            if _has_meaningful_anchor(cand):
                                anchor_json = cand
                            hdr_etag = _extract_snapshot_etag(retry_resp) or hdr_etag
                            logger.info(
                                "anchor_enrich_retry_used",
                                extra={"anchor_id": anchor_id, "has_payload": bool(_has_meaningful_anchor(anchor_json))},
                            )
                        except Exception:
                            # Preserve any previously good payload; do NOT clobber.
                            try:
                                logger.warning(
                                    "anchor_enrich_retry_failed_preserving_first",
                                    extra={
                                        "anchor_id": anchor_id,
                                        "timeout_ms": int(enrich_ms),
                                        "had_meaningful_before": _has_meaningful_anchor(anchor_json),
                                    },
                                )
                            except Exception:
                                pass
                            # If we still only had a stub, keep the stub, but don't overwrite a good one.
                            if not _has_meaningful_anchor(anchor_json):
                                anchor_json = {"id": anchor_id}
                                hdr_etag = "unknown"
                # Mirror option→title if needed (supports stubbed Memory API)
                try:
                    if anchor_json.get("option") and not anchor_json.get("title"):
                        anchor_json["title"] = anchor_json.get("option")
                except Exception:
                    pass
                # Also mirror title→option if option is empty (robustness for downstream consumers)
                try:
                    if (not anchor_json.get("option")) and isinstance(anchor_json.get("title"), str) and anchor_json["title"].strip():
                        anchor_json["option"] = anchor_json["title"]
                        logger.info("anchor_option_mirrored", extra={"anchor_id": anchor_id})
                except Exception:
                    pass
            async def _expand_neighbors():
                nonlocal neigh, meta
                try:
                    resp_neigh = await expand_client.post(
                        f"{settings.memory_api_url}/api/graph/expand_candidates",
                        json=plan,
                        headers=inject_trace_context(dict(policy_headers or {})),
                    )
                    if hasattr(resp_neigh, "raise_for_status"):
                        resp_neigh.raise_for_status()
                    try:
                        neigh = jsonx.loads(resp_neigh.content)
                    except Exception:
                        neigh = resp_neigh.json() or {}
                    meta = neigh.get("meta") or {}
                    policy_trace = neigh.get("policy_trace") or {}
                except Exception as exc:
                    # Add HTTP status/details when available (httpx.HTTPStatusError)
                    try:
                        status = getattr(getattr(exc, "response", None), "status_code", None)
                        detail = None
                        try:
                            detail = getattr(exc, "response").text[:200]  # avoid huge logs
                        except Exception:
                            detail = None
                        logger.warning(
                            "expand_candidates_failed",
                            extra={"anchor_id": anchor_id, "error": type(exc).__name__, "status": status, "detail": detail},
                        )
                    except Exception:
                        pass
                    neigh = {"neighbors": []}
                    meta = None

            logger.info("concurrent_fetch_start", extra={"anchor_id": anchor_id})
            await asyncio.gather(_fetch_anchor(), _expand_neighbors())
            logger.info("concurrent_fetch_done", extra={"anchor_id": anchor_id})

        # Default snapshot_etag to the anchor header etag; may be updated by expand meta.
        snapshot_etag = hdr_etag
        # (rest of the function continues unchanged; neigh/meta are now populated)

        meta = neigh.get("meta") or {}
        policy_trace = (locals().get("policy_trace") or {}) if "policy_trace" in locals() else {}
        # Strategic: show whether expand actually returned anything,
        # without dumping payloads.
        try:
            logger.info(
                "expand_result",
                extra={
                    "anchor_id": anchor_id,
                    "neighbor_count": len(neigh.get("neighbors") or []),
                    "meta_keys": list((meta or {}).keys()),
                },
            )
        except Exception:  # logging must never break the hot path
            pass
        meta_etag = None
        if isinstance(meta, dict):
            meta_etag = meta.get("snapshot_etag")
        if meta_etag:
            snapshot_etag = meta_etag

        try:
            if stale_ev and isinstance(anchor_json, dict):
                try:
                    cached_anchor = stale_ev.anchor.model_dump(mode="python", exclude_none=True)
                except Exception:
                    cached_anchor = {}
                # Fields we consider "descriptive" on the anchor. Keep source of truth in models.
                _allowed_anchor_fields = set(WhyDecisionAnchor.model_fields.keys())
                # Do not overwrite id; prefer fresh title if present.
                for k, v in (cached_anchor or {}).items():
                    if k in ("id",):
                        continue
                    # Only fill when the field is missing or empty in the fresh payload
                    cur = anchor_json.get(k, None)
                    is_empty = (cur is None) or (isinstance(cur, (list, str)) and len(cur) == 0)
                    if (k in _allowed_anchor_fields) and is_empty:
                        anchor_json[k] = v
                try:
                    logger.info("anchor_enrich_fallback_merged_from_cache", extra={"anchor_id": anchor_id})
                except Exception:
                    pass
        except Exception:
            pass

        events: list[dict] = []                    # event neighbours
        pre:    list[dict] = []                    # will hold classified preceding transitions
        suc:    list[dict] = []                    # will hold classified succeeding transitions

        neighbor_transitions: dict[str, dict] = {}
        neighbor_trans_orient: dict[str, str] = {}

        anchor_supported_ids: set[str] = set()
        event_led_to_map: dict[str, set[str]] = {}

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
                    # Determine declared entity type (lower-cased); default to empty string
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
           
            # Strategic logging: neighbor parse counts before dedup/enrich
            try:
                from .logging_helpers import stage as log_stage
                log_stage(
                    "evidence", "neighbor_parse",
                    anchor_id=anchor_id,
                    neighbors_raw=int(len(neighbors) if isinstance(neighbors, (list, dict)) else 0),
                    events_collected=len(events),
                    transitions_seen=len(neighbor_transitions),
                )
            except Exception:
                pass

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

            # Near-identical event collapse removed.
            try:
                from .logging_helpers import stage as log_stage
                log_stage("evidence", "near_identical_collapse_skipped", anchor_id=anchor_id)
            except Exception:
                pass

            # After deduplication, normalise monetary amounts on each event.
            try:
                for _ev in events:
                    try:
                        _normalise_event_amount(_ev)
                    except Exception:
                        continue
            except Exception:
                pass

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

        # Omit empty transition arrays from the final evidence (Pydantic excludes None).
        if not pre:
            pre = None
        if not suc:
            suc = None

        if events:
            with trace_span("enrich", anchor_id=anchor_id):
                enriched_events: list[dict] = []
                ev_client = get_http_client(timeout_ms=int(settings.timeout_enrich_ms))
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
                    path = f"{settings.memory_api_url}/api/enrich/event/{eid}"
                    try:
                        eresp = await ev_client.get(path, headers=inject_trace_context({}))
                        if hasattr(eresp, "raise_for_status"):
                            eresp.raise_for_status()
                        try:
                            parsed_ev = jsonx.loads(eresp.content)
                        except Exception:
                            parsed_ev = eresp.json() or {}
                        # Merge canonical event with original fields, favouring canonical keys
                        enriched_events.append({**parsed_ev, **ev})
                    except Exception:
                        logger.warning(
                            "event_enrich_failed",
                            extra={"event_id": eid, "anchor_id": anchor_id},
                        )
                        enriched_events.append(ev)
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


        declared_raw = anchor_json.get("transitions") or []
        declared_ids: set[str] = set()
        if isinstance(declared_raw, list):
            for x in declared_raw:
                if isinstance(x, str):
                    declared_ids.add(x)
                elif isinstance(x, dict):
                    tid = x.get("id")
                    if isinstance(tid, str):
                        declared_ids.add(tid)
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
            with trace_span("transitions_enrich", anchor_id=anchor_id):
                try:
                    tr_client = get_http_client(timeout_ms=int(settings.timeout_enrich_ms))
                    _title_cache: dict[str, str | None] = {}

                    async def _fetch_title(decision_id: str) -> str | None:
                        if not isinstance(decision_id, str):
                            return None
                        if decision_id in _title_cache:
                            return _title_cache[decision_id]
                        try:
                            dresp = await tr_client.get(
                                f"{settings.memory_api_url}/api/enrich/decision/{decision_id}",
                                headers=inject_trace_context(dict(policy_headers or {})),
                            )
                            if hasattr(dresp, "raise_for_status"):
                                dresp.raise_for_status()
                            try:
                                ddoc = jsonx.loads(dresp.content)
                            except Exception:
                                ddoc = dresp.json() or {}
                            title = ddoc.get("title") or ddoc.get("option")
                        except Exception:
                            title = None
                        _title_cache[decision_id] = title
                        return title
                    # First hydrate and classify transitions declared on the anchor
                    for tid in anchor_trans_ids:
                        if not isinstance(tid, str) or tid in seen_trans:
                            continue
                        seen_trans.add(tid)
                        tdoc: dict | None = None
                        # Attempt primary fetch
                        try:
                            resp = await tr_client.get(
                                f"{settings.memory_api_url}/api/enrich/transition/{tid}",
                                headers=inject_trace_context(dict(policy_headers or {})),
                            )
                            if hasattr(resp, "raise_for_status"):
                                resp.raise_for_status()
                            try:
                                tdoc = jsonx.loads(resp.content)
                            except Exception:
                                tdoc = resp.json() or {}
                        except Exception:
                            # Final fallback: use neighbour-provided stub if available
                            tdoc = neighbor_transitions.get(tid)
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
                        # Hydrate human titles to support templater 'Next:' pointer
                        try:
                            if isinstance(to_id, str):
                                tdoc.setdefault("to_title", await _fetch_title(to_id))
                            if isinstance(from_id, str):
                                tdoc.setdefault("from_title", await _fetch_title(from_id))
                        except Exception:
                            pass
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
                            resp = await tr_client.get(
                                f"{settings.memory_api_url}/api/enrich/transition/{tid}",
                                headers=inject_trace_context(dict(policy_headers or {})),
                            )
                            if hasattr(resp, "raise_for_status"):
                                resp.raise_for_status()
                            tdoc = resp.json() or {}
                        except Exception:
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
                        try:
                            if isinstance(to_id, str):
                                tdoc.setdefault("to_title", await _fetch_title(to_id))
                            if isinstance(from_id, str):
                                tdoc.setdefault("from_title", await _fetch_title(from_id))
                        except Exception:
                            pass
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

        # ── Enrich first succeeding transition with titles (to_title/from_title) ──
        try:
            if suc:
                _first = suc[0]
                # from_title: mirror the anchor's human label when available
                try:
                    _from_title = anchor_json.get("title") or anchor_json.get("option")
                    if _from_title and not _first.get("from_title"):
                        _first["from_title"] = _from_title
                except Exception:
                    pass
                # to_title: fetch decision doc if missing
                _to_id = _first.get("to") or _first.get("to_id")
                needs_title = not _first.get("to_title")
                if isinstance(_to_id, str) and _to_id and needs_title:
                    try:
                        _dec_client = get_http_client(timeout_ms=int(settings.timeout_enrich_ms))
                        dresp = await _dec_client.get(
                            f"{settings.memory_api_url}/api/enrich/decision/{_to_id}",
                            headers=inject_trace_context({}),
                        )
                        if hasattr(dresp, "raise_for_status"):
                            dresp.raise_for_status()
                        ddoc = dresp.json() or {}
                        _to_title = ddoc.get("title") or ddoc.get("option")
                        if _to_title:
                            _first["to_title"] = _to_title
                    except Exception:
                        try:
                            logger.info(
                                "transition_title_enrich_failed",
                                extra={"transition_id": _first.get("id"), "to": _to_id},
                            )
                        except Exception:
                            pass
                        try:
                            if isinstance(_to_id, str) and _to_id and not _first.get("to_title"):
                                # Replace common delimiters with spaces and capitalise each token.
                                human = " ".join(
                                    [w.capitalize() for w in re.split(r"[-_]+", _to_id) if w]
                                )
                                if human:
                                    _first["to_title"] = human
                        except Exception:
                            pass
        except Exception:
            # Best-effort enrichment — never fail the build pipeline on title lookup
            pass

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
                try:
                    _first_succ = (suc or [None])[0] or {}
                    _label = _first_succ.get("to_title") or _first_succ.get("title")
                    if _label:
                        logger.info("transition_titles_hydrated",
                                    extra={"anchor_id": anchor_id,
                                           "succeeding_id": _first_succ.get("id"),
                                           "to_title": _label})
                except Exception:
                    pass
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

        # Respect schema ownership in models: derive allowed keys from the model itself
        _allowed_anchor_fields = set(WhyDecisionAnchor.model_fields.keys())
        _incoming_anchor_keys = set((anchor_json or {}).keys())
        if _incoming_anchor_keys - _allowed_anchor_fields:
            try:
                logger.info(
                    "anchor_extra_fields_dropped",
                    extra={
                        "anchor_id": anchor_id,
                        "dropped": sorted([k for k in _incoming_anchor_keys - _allowed_anchor_fields]),
                    },
                )
            except Exception:
                pass
        _safe_anchor = {k: v for k, v in (anchor_json or {}).items() if k in _allowed_anchor_fields}
        # Strategic: normalise anchor fields for backward compatibility and log the operation.
        try:
            # Mirror option → title if upstream normaliser was skipped
            if _safe_anchor.get("option") and not _safe_anchor.get("title"):
                _safe_anchor["title"] = _safe_anchor["option"]
                logger.info("anchor_title_mirrored", extra={"anchor_id": anchor_id})
            # Mirror title → option if option is empty
            if (not _safe_anchor.get("option")) and isinstance(_safe_anchor.get("title"), str) and _safe_anchor["title"].strip():
                _safe_anchor["option"] = _safe_anchor["title"]
                logger.info("anchor_option_mirrored_postfilter", extra={"anchor_id": anchor_id})
            # Coerce transitions: list[str] → list[{"id": str}]
            t = _safe_anchor.get("transitions")
            if isinstance(t, list) and any(isinstance(x, str) for x in t):
                _safe_anchor["transitions"] = [{"id": x} for x in t if isinstance(x, str)]
                logger.info("anchor_transitions_coerced",
                            extra={"anchor_id": anchor_id, "count": len(_safe_anchor["transitions"])})
            # Presence telemetry for debugging regressions
            logger.info(
                "anchor_fields_presence",
                extra={
                    "anchor_id": anchor_id,
                    "has_option": bool(_safe_anchor.get("option")),
                    "has_rationale": bool(_safe_anchor.get("rationale")),
                    "has_timestamp": bool(_safe_anchor.get("timestamp")),
                    "has_decision_maker": bool(_safe_anchor.get("decision_maker")),
                },
            )
        except Exception:
            # Never break the hot path due to diagnostics
            pass
        _safe_anchor.setdefault("id", anchor_id)
        # If Memory returns transitions as list[str], drop them here.
        _tr = _safe_anchor.get("transitions")
        # If Memory returns transitions as list[str], drop them here (they will be hydrated below into preceding/succeeding).
        if isinstance(_tr, list) and _tr and not isinstance(_tr[0], dict):
            try:
                logger.info(
                    "anchor_transitions_dropped",
                    extra={"anchor_id": anchor_id, "len": len(_tr), "reason": "list_of_ids"},
                )
            except Exception:
                pass
            _safe_anchor.pop("transitions", None)
        anchor = WhyDecisionAnchor(**_safe_anchor)
        ev = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(preceding=pre, succeeding=suc),
            allowed_ids=_collect_allowed_ids(anchor, events, pre, suc),
            snapshot_etag=snapshot_etag,
        )

        ev.snapshot_etag = snapshot_etag
        ev.__dict__["_retry_count"] = retry_count
        selector_meta = {
            "prompt_truncation": False,
            "total_neighbors_found": max(len(ev.allowed_ids or []) - 1, 0),
            "final_evidence_count": len(ev.allowed_ids or []),
            "dropped_evidence_ids": [],
            "prompt_tokens": 0,
            "max_prompt_tokens": None,
            "bundle_size_bytes": 0,
        }
        if getattr(ev, "snapshot_etag", None) != snapshot_etag:
            ev.snapshot_etag = snapshot_etag
        try:
            pre_list = list(ev.transitions.preceding)
            suc_list = list(ev.transitions.succeeding)
        except Exception:
            pre_list, suc_list = [], []
        # Preserve full k=1 allowed_ids; do not recompute here (gating is applied only to the prompt).
        try:
            from .logging_helpers import stage as log_stage
            log_stage(
                "evidence", "allowed_ids_preserved",
                anchor_id=anchor_id,
                allowed_ids_count=len(ev.allowed_ids or []),
                events_after=len(ev.events or []),
                preceding_after=len(pre_list or []),
                succeeding_after=len(suc_list or []),
            )
        except Exception:
            pass
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
        # ── Attach policy_trace (if provided by Memory API) for audit drawer surfacing ──
        try:
            ev.__dict__["_policy_trace"] = policy_trace or {}
            # strategic breadcrumb for observability
            try:
                hv = (policy_trace or {}).get("counts", {}).get("hidden_vertices", 0)
                he = (policy_trace or {}).get("counts", {}).get("hidden_edges", 0)
                logger.info("policy_trace_attached", extra={"anchor_id": anchor_id, "hidden_vertices": hv, "hidden_edges": he})
            except Exception:
                pass
        except Exception:
            pass

        # ── Defense-in-depth: cheap ACL re-check on sanitized docs (log-only) ──
        try:
            roles_hdr = (policy_headers or {}).get("X-User-Roles") or (policy_headers or {}).get("x-user-roles") or ""
            namespaces_hdr = (policy_headers or {}).get("X-User-Namespaces") or (policy_headers or {}).get("x-user-namespaces") or ""
            sens_hdr = (policy_headers or {}).get("X-Sensitivity-Ceiling") or (policy_headers or {}).get("x-sensitivity-ceiling") or ""
            roles = {r.strip() for r in str(roles_hdr).split(",") if r.strip()}
            namespaces = {n.strip() for n in str(namespaces_hdr).split(",") if n.strip()}
            sens_order = {"low": 0, "medium": 1, "high": 2}
            sens_ceiling = sens_order.get(str(sens_hdr).strip().lower(), 2)
            def _allowed(doc: dict) -> bool:
                try:
                    ra = set(doc.get("roles_allowed") or [])
                    if ra and roles and roles.isdisjoint(ra):
                        return False
                except Exception:
                    pass
                try:
                    ns = set(doc.get("namespaces") or [])
                    if ns and namespaces and namespaces.isdisjoint(ns):
                        return False
                except Exception:
                    pass
                try:
                    s = str(doc.get("sensitivity","")).lower()
                    if s and sens_order.get(s, 0) > sens_ceiling:
                        return False
                except Exception:
                    pass
                return True
            before_ct = len(ev.events or [])
            ev.events = [e for e in (ev.events or []) if (not isinstance(e, dict)) or _allowed(e)]
            dropped = before_ct - len(ev.events or [])
            if dropped > 0:
                try:
                    logger.info("gateway_acl_recheck_dropped", extra={"anchor_id": anchor_id, "dropped": int(dropped)})
                except Exception:
                    pass
        except Exception:
            pass

        truncated_flag = selector_meta.get("prompt_truncation", False)
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
                serialized = jsonx.dumps(cache_val)
                # Async-only writes; no executor or sync fallbacks
                await self._redis.setex(composite_key, ttl, ev.model_dump_json())
                try:
                    a = getattr(ev, "anchor", None)
                    core_ok = bool(getattr(a, "rationale", None) or getattr(a, "timestamp", None) or getattr(a, "decision_maker", None))
                    any_lists = any([
                        bool(getattr(a, "tags", []) or []),
                        bool(getattr(a, "supported_by", []) or []),
                        bool(getattr(a, "based_on", []) or []),
                        ])
                    if core_ok or any_lists:
                        await self._redis.setex(alias_key, ttl, serialized)
                    else:
                        try:
                            logger.info("cache_alias_skip_incomplete_anchor", extra={"anchor_id": anchor_id})
                        except Exception:
                            pass
                except Exception:
                    await self._redis.setex(alias_key, ttl, serialized)
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
        if not cached_etag:
            return True
        if cached_etag == "unknown":
            return False
        try:
            client = get_http_client(timeout_ms=50)
            url = f"{settings.memory_api_url}/api/enrich/decision/{anchor_id}"
            try:
                resp = await client.get(
                    url,
                    headers=inject_trace_context({"x-cache-etag-check": "1"}),
                )
            except Exception:
                resp = await client.get(url, headers=inject_trace_context({}))
            return _extract_snapshot_etag(resp) == cached_etag
        except Exception:
            try:
                logger.warning("etag_check_failed", extra={"anchor_id": anchor_id})
            except Exception:
                pass
            return False

    async def get_evidence(self, anchor_id: str, *, fresh: bool = False) -> WhyDecisionEvidence:
        return await self.build(anchor_id, fresh=fresh)
