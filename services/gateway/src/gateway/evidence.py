# Imports
from __future__ import annotations
import asyncio, hashlib, httpx

from typing import Any, Optional
from core_metrics import counter as _ctr
from core_cache.redis_client import get_redis_pool
from core_utils import jsonx
from core_config import get_settings
from core_logging import get_logger, trace_span, log_stage, current_request_id
from core_http.client import fetch_json, get_http_client
from core_observability.otel import inject_trace_context
from fastapi import HTTPException
from core_models_gen import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    GraphEdgesModel,
    MemoryMetaModel,
)
from .selector import bundle_size_bytes

# Configuration & constants
settings        = get_settings()
logger          = get_logger("gateway.evidence")

# Public API functions
__all__ = [
    "expand_graph",
    "WhyDecisionEvidence",
]

def _raise_as_http_exception(exc: httpx.HTTPStatusError) -> None:
    status = getattr(exc.response, "status_code", 502)
    # Prefer JSON detail; fall back to response text or str(exc) deterministically
    detail = None
    try:
        detail = exc.response.json().get("detail")
    except (ValueError, TypeError, AttributeError):
        detail = getattr(exc.response, "text", None) or str(exc)
    raise HTTPException(status_code=int(status), detail=detail)

async def expand_graph(decision_id: str, *, intent: str | None = None, k: int = 1, policy_headers: dict | None = None):
    settings = get_settings()
    payload = {"anchor": decision_id}
    try:
        data = await fetch_json(
            "POST",
            f"{settings.memory_api_url}/api/graph/expand_candidates",
            json=payload,
            stage="expand",
            headers=inject_trace_context(dict(policy_headers or {})),
        )
        # v3 contract: prefer edges-only graph view from Memory
        return data or {"graph": {"edges": []}, "meta": {}}
    except httpx.HTTPStatusError as exc:
        _raise_as_http_exception(exc)
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("expand_candidates_failed", extra={"anchor_id": decision_id, "error": type(exc).__name__})
        return {"graph": {"edges": []}, "meta": {}}


def _make_cache_key(ev) -> str:
    """
    Evidence (masked) key via core_cache:
      bv:gw:v1:evidence:{snapshot_etag}|{allowed_ids_fp}|{policy_fp}
    """
    from core_cache import keys as cache_keys
    try:
        meta = getattr(ev, 'meta', {}) or {}
        # allowed_ids_fp may be an attribute or dict entry
        allowed_ids_fp = (
            getattr(meta, 'allowed_ids_fp', "")
            or (meta.get('allowed_ids_fp') if isinstance(meta, dict) else "")
        )
        # policy_fp: use meta.policy_fp only
        policy_val = None
        try:
            policy_val = getattr(meta, 'policy_fp', None)
        except Exception:
            policy_val = None
        if not policy_val and isinstance(meta, dict):
            policy_val = meta.get('policy_fp')
        policy_fp = str(policy_val or "")
    except (AttributeError, KeyError, TypeError, ValueError):
        allowed_ids_fp = ""
        policy_fp = ""
    return cache_keys.evidence(ev.snapshot_etag or "unknown", allowed_ids_fp, policy_fp)

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
    except (AttributeError, TypeError, ValueError):
        items = []

    for k, v in items:
        try:
            key = str(k).lower().replace("-", "_")
        except (AttributeError, ValueError):
            continue
        if key in ("snapshot_etag", "x_snapshot_etag"):
            return str(v)
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
            except (RuntimeError, OSError):
                self._redis = None

    async def build(
        self,
        anchor_id: str,
        *,
        intent: str = "why_decision",
        scope: str = "k1",
        fresh: bool = False,
        policy_headers: dict | None = None,
    ) -> WhyDecisionEvidence:
        # No alias/simple pre-read cache (Baseline v3). Fresh bypass is audit-logged.
        if fresh:
            log_stage(logger, "cache", "bypass",
                      anchor_id=anchor_id, reason="fresh=true",
                      request_id=(current_request_id() or "unknown"))

        with trace_span("gateway.plan", logger=logger, anchor_id=anchor_id):
            plan = {"anchor": anchor_id}
            # Use configured per-stage budgets; defaults already come from constants/env.
            enrich_ms = int(settings.timeout_enrich_ms)
            expand_ms = int(settings.timeout_expand_ms)
            enrich_client = get_http_client(timeout_ms=int(enrich_ms))
            expand_client = get_http_client(timeout_ms=int(expand_ms))
            # Concurrently fetch anchor and neighbors; each task handles its own errors
            anchor_json: dict = {"id": anchor_id}
            hdr_etag: str = "unknown"
            # Edges-only contract: default shape is graph.edges (no neighbors/transitions)
            neigh: dict = {"graph": {"edges": []}}
            meta: dict | None = None
            policy_trace: dict | None = None

            def _has_meaningful_anchor(d: dict) -> bool:
                return bool(
                    d.get("title")
                    or d.get("description")
                    or d.get("timestamp")
                    or d.get("decision_maker")
                )

            async def _fetch_anchor():
                nonlocal anchor_json, hdr_etag
                try:
                    # Use query params to ensure '#' in anchors is correctly URL-encoded (%23).
                    # This aligns with Baseline §0.2 (resolver canonicalizes only the string; no read-time mutation).
                    anchor_json = await fetch_json(
                        "GET",
                        f"{settings.memory_api_url}/api/enrich",
                        params={"anchor": anchor_id},
                        headers=inject_trace_context(dict(policy_headers or {})),
                        stage="enrich",
                    )
                    # Memory mirrors snapshot in headers/body; prefer header path if present
                    hdr_etag = str((anchor_json or {}).get("meta", {}).get("snapshot_etag") or "unknown")
                except httpx.HTTPStatusError as exc:
                    # Fail-closed: propagate Memory's denial (403/404/412...) to the client
                    _raise_as_http_exception(exc)
                except (OSError, asyncio.TimeoutError, ValueError) as exc:
                    try:
                        logger.warning(
                            "anchor_enrich_failed",
                            extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                        )
                        _ctr("gateway_anchor_enrich_fail_total", 1)
                    except (TypeError, ValueError):
                        pass
                    # Keep minimal stub on error; no jitter/retry in single-pass build.
                    if not _has_meaningful_anchor(anchor_json):
                        anchor_json = {"id": anchor_id}
                        hdr_etag = "unknown"
            async def _expand_neighbors():
                nonlocal neigh, meta, policy_trace
                try:
                    neigh = await fetch_json(
                        "POST",
                        f"{settings.memory_api_url}/api/graph/expand_candidates",
                        json=plan,
                        headers=inject_trace_context(dict(policy_headers or {})),
                        stage="expand",
                    )

                    meta = neigh.get("meta") or {}
                    policy_trace = neigh.get("policy_trace") or {}
                except httpx.HTTPStatusError as exc:
                    # Fail-closed: propagate Memory's denial (403/404/412...) to the client
                    _raise_as_http_exception(exc)
                except (OSError, asyncio.TimeoutError, ValueError) as exc:
                    # Narrow failure: surface single structured log (no stack spam)
                    logger.warning(
                        "expand_candidates_failed",
                        extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                    )
                    neigh = {"graph": {"edges": []}}
                    meta = None

            log_stage(logger, "evidence", "concurrent_fetch_start", anchor_id=anchor_id)
            await asyncio.gather(_fetch_anchor(), _expand_neighbors())
            log_stage(logger, "evidence", "concurrent_fetch_done", anchor_id=anchor_id)

        snapshot_etag = hdr_etag
        if isinstance(neigh, dict) and "graph" not in neigh and "edges" in neigh:
            neigh = {
                "graph": {"edges": neigh.get("edges")},
                "meta": neigh.get("meta", {}),
                **{k: v for k, v in neigh.items() if k not in ("edges", "meta")},
            }
        meta = neigh.get("meta") or {}
        policy_trace = (locals().get("policy_trace") or {}) if "policy_trace" in locals() else {}
        # Strategic: show whether expand actually returned anything (no payloads).
        log_stage(
            logger, "evidence", "expand_result",
            anchor_id=anchor_id,
            edge_count=len(((neigh.get("graph") or {}).get("edges") or [])),
            meta_keys=list((meta or {}).keys()),
        )
        meta_etag = None
        if isinstance(meta, dict):
            meta_etag = meta.get("snapshot_etag")
        if meta_etag:
            snapshot_etag = meta_etag
            pass

        # Build anchor; model enforces schema.
        _safe_anchor = dict(anchor_json or {})
        log_stage(
            logger, "evidence", "anchor_fields_presence",
            anchor_id=anchor_id,
            has_timestamp=bool(_safe_anchor.get("timestamp")),
            has_decision_maker=bool(_safe_anchor.get("decision_maker")),
        )
        _safe_anchor.setdefault("id", anchor_id)
        # v3: do not rely on anchor.transitions; neighbor **edges** drive orientation
        _safe_anchor.pop("transitions", None)
        _safe_anchor.pop("mask_summary", None)
        _edge_types = {
            str((e or {}).get("type") or "").upper()
            for e in ((neigh.get("graph") or {}).get("edges") or [])
            if isinstance(e, dict) and (e.get("type") is not None)
        }
        log_stage(logger, "evidence", "edge_types_used",
                  anchor_id=anchor_id, edge_types_used=sorted([t for t in _edge_types if t]))
        anchor = WhyDecisionAnchor(**_safe_anchor)
        ev = WhyDecisionEvidence(
            anchor=anchor,
            # STAGE 2.5: Memory-authoritative allowed_ids (no widening)
            allowed_ids=(meta.get('allowed_ids') if isinstance(meta, dict) and meta.get('allowed_ids') is not None else [anchor_id]),
            snapshot_etag=snapshot_etag,
        )
        # Attach edges-only graph when provided by Memory (v3) as typed field
        graph_edges: list[dict] = []
        if isinstance(neigh, dict) and isinstance(neigh.get("graph"), dict):
            raw_edges = neigh["graph"].get("edges") or []
            graph_edges = [e for e in raw_edges if isinstance(e, dict)]

        # Clamp to allowed_ids and strip orientation on ALIAS_OF; then de-duplicate
        _pool = set(ev.allowed_ids or [])
        kept: list[dict] = []
        if _pool:
            for _e in graph_edges:
                _f = _e.get("from") or _e.get("from_id")
                _t = _e.get("to") or _e.get("to_id")
                if (_f in _pool) and (_t in _pool):
                    if str(_e.get("type") or "").upper() == "ALIAS_OF":
                        _e.pop("orientation", None)
                    kept.append(_e)
        # De-duplicate by id or (type, from, to, timestamp) – Baseline §5.6/§15
        seen_ids: set[str] = set()
        seen_keys: set[tuple] = set()
        deduped: list[dict] = []
        for e in kept:
            key = (str(e.get("type") or ""), e.get("from") or e.get("from_id"), e.get("to") or e.get("to_id"), e.get("timestamp"))
            eid = e.get("id")
            if eid and eid in seen_ids:
                continue
            if key in seen_keys:
                continue
            if eid:
                seen_ids.add(eid)
            seen_keys.add(key)
            deduped.append(e)
        # Metrics: count duplicate inputs rejected (edges)
        dropped_dups = max(0, len(kept) - len(deduped))
        if dropped_dups:
            _ctr("gateway_duplicate_inputs_rejected_total", dropped_dups, kind="edge")
        ev.graph = GraphEdgesModel(edges=deduped)
        try:
            log_stage(logger, "evidence", "evidence_clamped_to_pool", anchor_id=anchor_id, edges_after=len(deduped))
        except (TypeError, ValueError, AttributeError):
            pass

        # Strategic log: record allowed_ids provenance (+ fingerprints)
        try:
            _src = 'memory' if (isinstance(meta, dict) and meta.get('allowed_ids') is not None) else 'fallback_minimal'
            _fp  = 'sha256:' + hashlib.sha256('|'.join(sorted(ev.allowed_ids or [])).encode('utf-8')).hexdigest() if (ev.allowed_ids is not None) else ''
            # Log the allowed_ids fingerprint and policy fingerprint.
            _policy_fp_logged = (meta or {}).get('policy_fp')
            log_stage(logger, "evidence", "allowed_ids_source",
                        extra={'anchor_id': anchor_id, 'source': _src, 'fp': _fp, 'etag': snapshot_etag,
                               'allowed_ids_fp': (meta or {}).get('allowed_ids_fp'),
                               'policy_fp': _policy_fp_logged})
        except (TypeError, ValueError, AttributeError):
            pass
        # Surface Memory meta as typed field (extras forbidden)
        # Attach Memory meta as a plain dict (light shaping; validator enforces schema downstream).
        try:
            ev.meta = MemoryMetaModel(**(meta or {}))
        except (TypeError, ValueError) as exc:
            # Deterministic fallback + structured warn; never silent
            logger.warning("memory_meta_validation_failed",
                           extra={"anchor_id": anchor_id, "error": type(exc).__name__})
            ev.meta = MemoryMetaModel(snapshot_etag=snapshot_etag)
        ev.snapshot_etag = snapshot_etag
        # Strategic: meta typed; include fingerprints for audit (no secrets)
        try:
            logger.info("memory_meta_typed", extra={
                "anchor_id": anchor_id,
                "policy_fp": getattr(ev.meta, "policy_fp", None),
                "allowed_ids_fp": getattr(ev.meta, "allowed_ids_fp", None)})
        except Exception:
            pass
        # Minimal structured observability without dynamic __dict__ mutations
        try:
            log_stage(
                logger, "evidence", "allowed_ids_preserved",
                anchor_id=anchor_id,
                allowed_ids_count=len(ev.allowed_ids or []),
                preceding_after=0,
                succeeding_after=0,
            )
        except (TypeError, ValueError, AttributeError):
            pass

        # Events list and ACL re-check removed; graph.edges is authoritative.

        composite_key  = _make_cache_key(ev)
        if self._redis:
            try:
                from core_config.constants import TTL_EVIDENCE_CACHE_SEC as _TTL_EVIDENCE_S
                # Async-only writes; no executor or sync fallbacks
                # Persist the primary evidence under the composite key
                await self._redis.setex(composite_key, _TTL_EVIDENCE_S, ev.model_dump_json())
                try:
                    # Strategic logging: evidence cache stored (policy/ids/snapshot all in key)
                    logger.info(
                        "evidence_cache_store",
                        extra={
                            "anchor_id": anchor_id,
                            "snapshot_etag": ev.snapshot_etag or "unknown",
                            "allowed_ids_fp": getattr(getattr(ev, "meta", {}), "allowed_ids_fp", None)
                                            or (getattr(ev, "meta", {}) or {}).get("allowed_ids_fp"),
                            "policy_fp": getattr(getattr(ev, "meta", {}), "policy_fp", None)
                                         or (getattr(ev, "meta", {}) or {}).get("policy_fp"),
                        },
                    )
                except (TypeError, ValueError, AttributeError):
                    pass
            except (asyncio.TimeoutError, OSError, RuntimeError, ValueError, TypeError):
                logger.warning("redis write error", exc_info=True)
                self._redis = None
        logger.info(
            "evidence_built",
            extra={"anchor_id": anchor_id, "bundle_size_bytes": bundle_size_bytes(ev)},
        )

        return ev

    async def _is_fresh(self, anchor_id: str, cached_etag: str, policy_headers: dict | None = None) -> bool:
        if not cached_etag:
            return True
        if cached_etag == "unknown":
            return False
        try:
            client = get_http_client(timeout_ms=50)
            url = f"{settings.memory_api_url}/api/enrich"
            # Use HEAD + If-None-Match to avoid duplicate GET body + logs.
            resp = await client.head(
                url,
                params={"anchor": anchor_id},
                headers=inject_trace_context({**dict(policy_headers or {}), "If-None-Match": cached_etag}),
            )
            fresh = False
            try:
                # 304 means fresh; otherwise compare returned ETag header defensively.
                if getattr(resp, "status_code", 0) == 304:
                    fresh = True
                else:
                    fresh = (_extract_snapshot_etag(resp) == cached_etag)
            except Exception:
                fresh = False
            try:
                logger.info("etag_head_check", extra={"anchor_id": anchor_id, "fresh": bool(fresh)})
            except Exception:
                pass
            return fresh
        except (OSError, asyncio.TimeoutError, ValueError, TypeError):
            try:
                logger.warning("etag_check_failed", extra={"anchor_id": anchor_id})
            except (TypeError, ValueError, AttributeError):
                pass
            return False

    async def get_evidence(self, anchor_id: str, *, fresh: bool = False) -> WhyDecisionEvidence:
        return await self.build(anchor_id, fresh=fresh)
