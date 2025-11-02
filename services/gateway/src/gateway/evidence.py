# Imports
from __future__ import annotations
import asyncio, httpx
from core_utils.fingerprints import allowed_ids_fp, ensure_sha256_prefix, graph_fp as compute_graph_fp
from typing import Any, Optional
from core_metrics import counter as _ctr
from core_cache.redis_client import get_redis_pool
from core_utils import jsonx
from core_config import get_settings
from core_logging import get_logger, trace_span, log_stage, current_request_id
from core_http.client import fetch_json, get_http_client
from core_http.headers import BV_POLICY_FP, BV_ALLOWED_IDS_FP, REQUEST_SNAPSHOT_ETAG
from core_observability.otel import inject_trace_context
from fastapi import HTTPException
from core_http.errors import raise_http_error
from core_logging.error_codes import ErrorCode
from core_models_gen import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    GraphEdgesModel,
    MemoryMetaModel,
)
from .selector import bundle_size_bytes
from core_http.headers import RESPONSE_SNAPSHOT_ETAG, IF_NONE_MATCH
from core_models.ontology import canonical_edge_type

settings        = get_settings()
logger          = get_logger("gateway.evidence")

# Public API functions
__all__ = [
    "expand_graph",
    "WhyDecisionEvidence",
]

def _raise_as_http_exception(exc: httpx.HTTPError, *, url: str | None = None, stage: str | None = None) -> None:
     """
     Map httpx exceptions to client-facing HTTP errors.
     - HTTPStatusError: preserve upstream status + detail
     - Request/Connect errors: surface as 502 upstream_error with a compact message
     """
     if isinstance(exc, httpx.HTTPStatusError):
         status = int(getattr(exc.response, "status_code", 502) or 502)
         # Prefer JSON detail; fall back deterministically.
         try:
             detail = exc.response.json().get("detail")
         except (ValueError, TypeError, AttributeError):
             detail = getattr(exc.response, "text", None) or str(exc)
         # Optional UX: nudge on snapshot precondition failures.
         if status == 412 and isinstance(detail, str) and detail.startswith("precondition:"):
             detail = f"{detail} (hint: HEAD /api/enrich?anchor=<id> → use X-Snapshot-ETag)"
         raise HTTPException(status_code=status, detail=detail)
     # Transport-layer errors (DNS, connect, TLS, etc.)
     try:
         logger.warning(
             "upstream_request_error",
             extra={"url": (url or "?"), "stage": (stage or "?"), "error": type(exc).__name__},
         )
     except (TypeError, ValueError):
         pass
     raise raise_http_error(
         502, ErrorCode.upstream_error, f"{stage or 'upstream'} request failed",
         request_id=(current_request_id() or "unknown"),
     )

async def expand_graph(decision_id: str, *, intent: str | None = None, k: int = 1, policy_headers: dict | None = None):
    settings = get_settings()
    payload = {"anchor": decision_id}
    # Snapshot-bind: fetch the current ETag via HEAD, then send it as X-Snapshot-ETag.
    try:
        client = get_http_client(timeout_ms=int(settings.timeout_expand_ms))
        head_resp = await client.request(
            "HEAD",
            f"{settings.memory_api_url}/api/enrich",
            params={"anchor": decision_id},
            headers=inject_trace_context(dict(policy_headers or {})),
        )
        hdr_etag = _extract_snapshot_etag(head_resp)
    except (httpx.HTTPStatusError, httpx.RequestError, OSError, asyncio.TimeoutError, ValueError):
        hdr_etag = "unknown"
    try:
        data = await fetch_json(
            "POST",
            f"{settings.memory_api_url}/api/graph/expand_candidates",
            json=payload,
            stage="expand",
            headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: hdr_etag, **(dict(policy_headers or {}))}),
        )
        # v3 contract: prefer edges-only graph view from Memory
        return data or {"graph": {"edges": []}, "meta": {}}
    except httpx.HTTPStatusError as exc:
        # Single retry on snapshot drift: prefer the server's advertised ETag.
        if exc.response is not None and exc.response.status_code == 412:
            new_etag = _extract_snapshot_etag(exc.response) or "unknown"
            if new_etag and new_etag != hdr_etag:
                data = await fetch_json(
                    "POST",
                    f"{settings.memory_api_url}/api/graph/expand_candidates",
                    json=payload,
                    stage="expand",
                    headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: new_etag, **(dict(policy_headers or {}))}),
                )
                return data or {"graph": {"edges": []}, "meta": {}}
        _raise_as_http_exception(exc, url=f"{settings.memory_api_url}/api/graph/expand_candidates", stage="expand")
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("expand_candidates_failed", extra={"anchor_id": decision_id, "error": type(exc).__name__})
        raise raise_http_error(502, ErrorCode.upstream_error, "expand_candidates failed", request_id=(current_request_id() or "unknown"))


def _make_cache_key(ev) -> str:
    """
    Evidence (masked) key via core_cache:
      bv:gw:v1:evidence:{snapshot_etag}|{allowed_ids_fp}|{policy_fp}
    """
    from core_cache import keys as cache_keys
    try:
        meta = getattr(ev, "meta", {}) or {}
        # Pull from model or dict; normalise to str and ensure 'sha256:' prefix when present.
        ids_fp = (
            getattr(meta, "allowed_ids_fp", "")
            or (meta.get("allowed_ids_fp") if isinstance(meta, dict) else "")
        )
        pol_fp = None
        try:
            pol_fp = getattr(meta, "policy_fp", None)
        except AttributeError:
            pol_fp = None
        if pol_fp is None and isinstance(meta, dict):
            pol_fp = meta.get("policy_fp")
        ids_fp = str(ids_fp or "")
        pol_fp = str(pol_fp or "")
        if ids_fp:
            ids_fp = ensure_sha256_prefix(ids_fp)
        if pol_fp:
            pol_fp = ensure_sha256_prefix(pol_fp)
    except (AttributeError, KeyError, TypeError, ValueError):
        ids_fp = ""
        pol_fp = ""
    return cache_keys.evidence(str(ev.snapshot_etag or "unknown"), ids_fp, pol_fp)

def _extract_snapshot_etag(resp: Any) -> str:
    """
    Extract the mirrored snapshot ETag from a Memory response.
    Contract: Memory replies with the lowercase header 'x-snapshot-etag'.
    """
    headers = getattr(resp, "headers", None)
    if headers and hasattr(headers, "items"):
        try:
            lower = {str(k).lower(): str(v) for k, v in headers.items()}
            # Look up using a lower-cased key to avoid case drift.
            key = str(RESPONSE_SNAPSHOT_ETAG).lower()
            etag = lower.get(key)
            if not etag:
                # Fallback: accept plain HTTP ETag as a last resort (strip quotes)
                raw = lower.get("etag")
                if raw:
                    etag = raw.strip('"')
            return etag or "unknown"
        except (AttributeError, TypeError, ValueError):
            # fall through to "unknown"
            pass
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
            # Probe snapshot etag via cheap HEAD to snapshot-bind subsequent reads
            try:
                head_resp = await expand_client.request(
                    "HEAD",
                    f"{settings.memory_api_url}/api/enrich",
                    params={"anchor": anchor_id},
                    headers=inject_trace_context(dict(policy_headers or {})),
                )
                hdr_etag = _extract_snapshot_etag(head_resp)
            except (httpx.HTTPStatusError, httpx.RequestError, OSError, asyncio.TimeoutError, ValueError):
                hdr_etag = "unknown"

            # Concurrently fetch anchor and neighbors; each task handles its own errors
            anchor_json: dict = {"id": anchor_id}
            # Edges-only contract: default shape is graph.edges (no neighbors/transitions)
            neigh: dict = {"graph": {"edges": []}}
            meta: dict | None = None
            policy_trace: dict | None = None
            _hdrs_anchor: dict | None = None
            _hdrs_expand: dict | None = None
            

            def _has_meaningful_anchor(d: dict) -> bool:
                return bool(
                    d.get("title")
                    or d.get("description")
                    or d.get("timestamp")
                    or d.get("decision_maker")
                )

            async def _fetch_anchor():
                nonlocal anchor_json, hdr_etag, _hdrs_anchor
                try:
                    anchor_json, _hdrs_anchor = await fetch_json(
                        "GET",
                        f"{settings.memory_api_url}/api/enrich",
                        params={"anchor": anchor_id},
                        headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: hdr_etag, **(dict(policy_headers or {}))}),
                        stage="enrich",
                        return_headers=True,
                    )
                    # Do not overwrite the snapshot etag learned via HEAD; enrich mirrors it in headers.
                    # (enrich does not include snapshot_etag in the JSON body by design).
                except httpx.HTTPStatusError as exc:
                    # Retry once on snapshot drift using the ETag from the 412 response
                    if exc.response is not None and exc.response.status_code == 412:
                        new_etag = _extract_snapshot_etag(exc.response) or "unknown"
                        if new_etag and new_etag != hdr_etag:
                            anchor_json = await fetch_json(
                                "GET",
                                f"{settings.memory_api_url}/api/enrich",
                                params={"anchor": anchor_id},
                                headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: new_etag, **(dict(policy_headers or {}))}),
                                stage="enrich",
                            )
                            return
                    # Otherwise, propagate
                    _raise_as_http_exception(exc, url=f"{settings.memory_api_url}/api/enrich", stage="enrich")
                except httpx.RequestError as exc:
                    _raise_as_http_exception(exc, url=f"{settings.memory_api_url}/api/enrich", stage="enrich")
                except (OSError, asyncio.TimeoutError, ValueError) as exc:
                    try:
                        logger.warning(
                            "anchor_enrich_failed",
                            extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                        )
                        _ctr("gateway_anchor_enrich_fail_total", 1)
                    except (TypeError, ValueError):
                        pass
                    raise raise_http_error(502, ErrorCode.upstream_error, "anchor enrich failed", request_id=(current_request_id() or "unknown"))
            async def _expand_neighbors():
                nonlocal neigh, meta, policy_trace, _hdrs_expand
                nonlocal hdr_etag
                try:
                    neigh, _hdrs_expand = await fetch_json(
                        "POST",
                        f"{settings.memory_api_url}/api/graph/expand_candidates",
                        json=plan,
                        headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: hdr_etag, **(dict(policy_headers or {}))}),
                        stage="expand",
                        return_headers=True,
                    )

                    meta = neigh.get("meta") or {}
                    policy_trace = neigh.get("policy_trace") or {}
                except httpx.HTTPStatusError as exc:
                    # Retry once on snapshot drift using the ETag from the 412 response
                    if exc.response is not None and exc.response.status_code == 412:
                        new_etag = _extract_snapshot_etag(exc.response) or "unknown"
                        if new_etag and new_etag != hdr_etag:
                            neigh = await fetch_json(
                                "POST",
                                f"{settings.memory_api_url}/api/graph/expand_candidates",
                                json=plan,
                                headers=inject_trace_context({REQUEST_SNAPSHOT_ETAG: new_etag, **(dict(policy_headers or {}))}),
                                stage="expand",
                            )
                            meta = neigh.get("meta") or {}
                            policy_trace = neigh.get("policy_trace") or {}
                            return
                    # Otherwise, propagate
                    _raise_as_http_exception(exc, url=f"{settings.memory_api_url}/api/graph/expand_candidates", stage="expand")
                except httpx.RequestError as exc:
                    _raise_as_http_exception(exc, url=f"{settings.memory_api_url}/api/graph/expand_candidates", stage="expand")
                except (OSError, asyncio.TimeoutError, ValueError) as exc:
                    logger.warning(
                        "expand_candidates_failed",
                        extra={"anchor_id": anchor_id, "error": type(exc).__name__},
                    )
                    raise raise_http_error(
                        502, ErrorCode.upstream_error, "expand_candidates failed",
                        request_id=(current_request_id() or "unknown")
                    )

            log_stage(logger, "evidence", "concurrent_fetch_start", anchor_id=anchor_id)
            await asyncio.gather(_fetch_anchor(), _expand_neighbors())
            log_stage(logger, "evidence", "concurrent_fetch_done", anchor_id=anchor_id)

        # ── Light gate: fail-closed on policy fingerprint mismatch (schema-agnostic) ──
        # Headers may vary in case; prefer canonical constants and fallback to lowercase dict lookup.
        def _get_hdr(h: dict | None, key: str) -> str | None:
            if not h:
                return None
            return h.get(key) or h.get(str(key).lower()) or h.get(str(key).upper())

        _pfp_anchor = _get_hdr(_hdrs_anchor, BV_POLICY_FP)
        _pfp_expand = _get_hdr(_hdrs_expand, BV_POLICY_FP)
        if _pfp_anchor and _pfp_expand and _pfp_anchor != _pfp_expand:
            _aid_expand = _get_hdr(_hdrs_expand, BV_ALLOWED_IDS_FP)
            log_stage(
               logger, "policy", "policy_fp_mismatch_expand",
                anchor_id=anchor_id,
                policy_fp_anchor=_pfp_anchor,
                policy_fp_expand=_pfp_expand,
                allowed_ids_fp_expand=_aid_expand,
                request_id=(current_request_id() or "unknown"),
            )
            # Fail-closed with a JSON-first, stable error model
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "policy_fp_mismatch",
                    "message": "Anchor and expand computed under different policy fingerprints.",
                    "details": {
                        "policy_fp_anchor": _pfp_anchor,
                        "policy_fp_expand": _pfp_expand,
                        "allowed_ids_fp_expand": _aid_expand,
                    },
                },
            )

        snapshot_etag = hdr_etag
        if isinstance(neigh, dict) and "graph" not in neigh and "edges" in neigh:
            neigh = {
                "graph": {"edges": neigh.get("edges")},
                "meta": neigh.get("meta", {}),
                **{k: v for k, v in neigh.items() if k not in ("edges", "meta")},
            }
        meta = neigh.get("meta") or {}
        policy_trace = policy_trace or {}
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
                    if canonical_edge_type(_e.get("type")) == "ALIAS_OF":
                        _e.pop("orientation", None)
                    kept.append(_e)
        else:
            # No pool → graph is already maximal for this anchor
            log_stage(
                logger,
                "evidence",
                "graph_pool_empty",
                anchor_id=anchor_id,
                original_edges=len(graph_edges),
            )
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
            log_stage(
                logger,
                "evidence",
                "graph_edges_deduped",
                anchor_id=anchor_id,
                kept=len(kept),
                deduped=len(deduped),
                dropped=dropped_dups,
            )
        # Verify Memory's graph_fp by recomputing over deduped, pool-clamped edges (PR-2).
        # Proceed with the recomputed value regardless, and log verification result.
        _mem_graph_fp = ""
        _fps = {}
        if isinstance(meta, dict):
            _fps = (meta.get("fingerprints") or {})
            try:
                _mem_graph_fp = str(_fps.get("graph_fp") or "")
            except (TypeError, ValueError):
                _mem_graph_fp = ""
        try:
            _recomputed_graph_fp = compute_graph_fp(anchor_id, deduped)
        except (TypeError, ValueError) as exc:
            # Targeted failure: log and skip verification; do not mask with broad exceptions.
            try:
                logger.warning(
                    "graph_fp_recompute_failed",
                    extra={
                        "anchor_id": anchor_id,
                        "error": type(exc).__name__,
                        "snapshot_etag": snapshot_etag,
                    },
                )
                _ctr("gateway_graph_fp_recompute_fail_total", 1)
            except (TypeError, ValueError):
                pass
            _recomputed_graph_fp = _mem_graph_fp or ""
        # Compare against Memory's body value (and override meta to the recomputed fp).
        if _recomputed_graph_fp:
            if _mem_graph_fp and (_mem_graph_fp != _recomputed_graph_fp):
                try:
                    log_stage(logger, 'builder', 'builder.graph_fp_mismatch',
                                 anchor_id=anchor_id,
                                 memory_fp=_mem_graph_fp,
                                 recomputed_fp=_recomputed_graph_fp,
                                 snapshot_etag=snapshot_etag)
                    _ctr("gateway_graph_fp_mismatch_total", 1)
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    log_stage(logger, 'builder', 'builder.graph_fp_verified',
                                anchor_id=anchor_id,
                                graph_fp=_recomputed_graph_fp)
                    _ctr("gateway_graph_fp_verified_total", 1)
                except (TypeError, ValueError):
                    pass
            # Proceed using the recomputed value (fail-safe if meta is not dict).
            if isinstance(meta, dict):
                _fps = (meta.get("fingerprints") or {})
                _fps["graph_fp"] = _recomputed_graph_fp
                meta["fingerprints"] = _fps
        ev.graph = GraphEdgesModel(edges=deduped)
        try:
            log_stage(logger, "evidence", "evidence_clamped_to_pool", anchor_id=anchor_id, edges_after=len(deduped))
        except (TypeError, ValueError, AttributeError):
            pass

        # Strategic log: record allowed_ids provenance (+ fingerprints)
        try:
            _src = 'memory' if (isinstance(meta, dict) and meta.get('allowed_ids') is not None) else 'fallback_minimal'
            _fp  = allowed_ids_fp(ev.allowed_ids or []) if (ev.allowed_ids is not None) else ''
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
        except (TypeError, ValueError, AttributeError):
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
                headers=inject_trace_context({**dict(policy_headers or {}), IF_NONE_MATCH: cached_etag}),
            )
            fresh = False
            try:
                # 304 means fresh; otherwise compare returned ETag header defensively.
                if getattr(resp, "status_code", 0) == 304:
                    fresh = True
                else:
                    fresh = (_extract_snapshot_etag(resp) == cached_etag)
            except (AttributeError, TypeError, ValueError):
                fresh = False
            try:
                logger.info("etag_head_check", extra={"anchor_id": anchor_id, "fresh": bool(fresh)})
            except (TypeError, ValueError, AttributeError):
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
