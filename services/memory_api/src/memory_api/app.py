import asyncio
import time
import os
from typing import List, Optional, Mapping
from functools import lru_cache
import inspect
from pathlib import Path
import httpx
import core_models
from fastapi import FastAPI, Response, HTTPException, Request
from fastapi.responses import JSONResponse
from core_config import get_settings
from core_logging import get_logger, log_stage, trace_span
from core_utils.fastapi_bootstrap import setup_service
from core_utils.fingerprints import schema_dir_fp
from core_http.errors import attach_standard_error_handlers
from core_observability.otel import inject_trace_context
from core_storage import ArangoStore
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_policy_opa import opa_decide_if_enabled, OPADecision
from core_logging import log_once
from core_utils.domain import is_valid_anchor, anchor_to_storage_key, storage_key_to_anchor
from core_models.graph_view import to_wire_edges
from core_models.ontology import CAUSAL_EDGE_TYPES, ALIAS_EDGE_TYPES
from core_utils import jsonx
from core_utils.graph import alias_meta
from core_validator import validate_graph_view
from core_utils.fingerprints import graph_fp as fp_graph, allowed_ids_fp as fp_allowed_ids
from core_cache import keys as cache_keys
from core_cache.redis_cache import RedisCache
from core_cache.redis_client import get_redis_pool
from core_http.client import get_http_client
from core_config.constants import timeout_for_stage, TTL_EVIDENCE_CACHE_SEC
from core_metrics import histogram as metric_histogram
from .policy import compute_effective_policy, field_mask, field_mask_with_summary, acl_check, PolicyHeaderError

settings = get_settings()
logger = get_logger("memory_api")
logger.propagate = False

app = FastAPI(title="BatVault Memory_API", version="0.1.0")

# Resolve once per-process; avoids recomputation on hot path.
_SCHEMA_FP: str | None = None
def _schema_fp() -> str | None:
    global _SCHEMA_FP
    if _SCHEMA_FP is None:
        try:
            _SCHEMA_FP = schema_dir_fp(Path(core_models.__file__).parent / "schemas")
        except (FileNotFoundError, OSError, ValueError):
            _SCHEMA_FP = None
    return _SCHEMA_FP
setup_service(app, 'memory_api')
attach_standard_error_handlers(app, service="memory_api")

# ────────────────────────────────────────────────────────────
# M6 — stage timing helper (local to memory_api)
# ────────────────────────────────────────────────────────────
class _StageTimers:
    def __init__(self):
        self._t = {}
        self._elapsed = {}

    def start(self, name: str):
        self._t[name] = time.perf_counter()

    def stop(self, name: str):
        t0 = self._t.get(name)
        if t0 is not None:
            self._elapsed[name] = int((time.perf_counter() - t0) * 1000)

    def as_dict(self):
        return dict(self._elapsed)

    # context manager for convenience
    def ctx(self, name: str):
        class _Ctx:
            def __enter__(_self):
                self.start(name)
            def __exit__(_self, exc_type, exc, tb):
                self.stop(name)
        return _Ctx()

def _clear_store_cache() -> None:  # pragma: no cover – trivial utility
    """Best-effort cache invalidation that tolerates monkey-patched *store*."""
    clear_fn = getattr(store, "cache_clear", None)  # type: ignore[attr-defined]
    if callable(clear_fn):
        clear_fn()

def _policy_from_request_headers(h: Mapping[str, str]) -> dict:
    """Centralized policy parsing with consistent error mapping."""
    try:
        return compute_effective_policy({k: v for k, v in h.items()})
    except PolicyHeaderError as e:
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")

async def _ping_arango_ready() -> bool:
    """
    Return True iff ArangoDB responds OK. Memory API depends on ArangoDB.
    """
    settings = get_settings()
    try:
        client = get_http_client(timeout_ms=int(1000 * timeout_for_stage('enrich')))
        r = await client.get(f"{settings.arango_url}/_api/version", headers=inject_trace_context({}))
        return r.status_code == 200
    except (httpx.HTTPError, OSError, ValueError):
        return False
    
async def _ping_gateway_ready() -> bool:
    """Backward-compatible alias for tests; calls Arango readiness."""
    return await _ping_arango_ready()

async def _readiness() -> dict:
    """
    Tests monkey-patch ``_ping_gateway_ready`` with a *synchronous* lambda.
    Accept both sync & async call-sites.
    """
    res = _ping_arango_ready()
    ok = await res if inspect.isawaitable(res) else bool(res)
    return {
        "status": "ready" if ok else "degraded",
        "ready": bool(ok),
        "arango_ok": ok,
        "request_id": generate_request_id(),
    }

attach_health_routes(
    app,
    checks={
        "liveness": lambda: True,
        "readiness": _readiness,
    },
)

@lru_cache()
def store() -> ArangoStore:
    # lazy=True prevents connection attempts during unit tests
    return ArangoStore(settings.arango_url,
                       settings.arango_root_user,
                       settings.arango_root_password,
                       settings.arango_db,
                       settings.arango_graph_name,
                       settings.arango_catalog_collection,
                       settings.arango_meta_collection,
                       lazy=True)

@app.on_event("startup")
async def bootstrap_arango():
    # Ensure DB/collections/views/indexes are created once at startup (fail-fast in non-DEV).
    try:
        st = store()
        # Triggers _connect() and idempotent bootstrap here, not on the hot read path.
        _ = st.get_snapshot_etag()
        log_stage(logger, "bootstrap", "arango_ready", status="ok", request_id="startup")
    except (RuntimeError, OSError, ValueError) as exc:
        # Startup-time audit; do not move this work to the read path.
        log_stage(logger, "bootstrap", "arango_ready_failed", error=str(exc), request_id="startup")

# ──────────────────────────────────────────────────────────────────────────────
# Shared helper: always attach the current snapshot ETag
# ──────────────────────────────────────────────────────────────────────────────

def _compute_allowed_ids(anchor: dict, edges: list) -> list[str]:
    """
    Compute nodes-only allowed_ids from the masked k=1 slice (edges-only).
    Include anchor.id and all `from`/`to` ids from edges.
    """
    ids: set[str] = set()
    aid = str((anchor or {}).get("id") or "").strip()
    if aid:
        ids.add(aid)
    for t in (edges or []):
        f = str((t or {}).get("from") or "").strip()
        if f:
            ids.add(f)
        to = str((t or {}).get("to") or "").strip()
        if to:
            ids.add(to)
    return sorted(ids)

def _json_response_with_etag(payload: dict, etag: Optional[str] = None) -> JSONResponse:
    """
    Build a JSONResponse and, when available, mirror the repository’s current
    snapshot ETag in the `x-snapshot-etag` header so that gateways and tests
    can rely on cache-invalidation semantics.
    """
    resp = JSONResponse(content=payload)
    if etag:
        resp.headers["x-snapshot-etag"] = etag
    sfp = _schema_fp()
    if sfp:
        resp.headers["X-BV-Schema-FP"] = sfp
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            fps = meta.get("fingerprints")
            pfp = (meta.get("policy_fp")
                   or (fps.get("policy_fingerprint") if isinstance(fps, dict) else None))
            if pfp:
                resp.headers["X-BV-Policy-Fingerprint"] = str(pfp)
            aid_fp = meta.get("allowed_ids_fp")
            if aid_fp:
                resp.headers["X-BV-Allowed-Ids-FP"] = str(aid_fp)
            if isinstance(fps, dict):
                gfp = fps.get("graph_fp")
                if isinstance(gfp, str) and gfp:
                    resp.headers["X-BV-Graph-FP"] = gfp
    return resp

def _attach_snapshot_meta(doc: dict, etag: Optional[str]) -> dict:
    return doc

# ------------------ Scope assembly (shared between expand & enrich) ------------------
def _edges_with_acl_and_alias_tail(
    st: ArangoStore, node_id: str, anchor_doc: dict, policy: dict, *, request_id: str | None = None
) -> List[dict]:
    """Assemble edges touching the anchor (k=1) that pass ACL checks and *also* the bounded
    alias tail (exactly one outbound hop in the alias_event.domain; newest first cap 3),
    returning a list of **storage-key** edges. Never computes orientation, never dedups/sorts.
    Dedup/sort/wire-shaping is centralized in core_models.graph_view.to_wire_edges to avoid drift.
    """
    # Edge allowlist (default: canonical three types)
    allowed_types = set((policy.get("edge_allowlist") or [])) if isinstance(policy, dict) else set()
    if not allowed_types:
        allowed_types = {"LED_TO", "CAUSAL", "ALIAS_OF"}

    edges_in: list = (st.get_edges_adjacent(node_id) or {}).get("edges") or []
    edges_kept: list = []

    for e in edges_in:
        et = (e or {}).get("type")
        if et not in allowed_types:
            continue
        f = (e or {}).get("from"); t = (e or {}).get("to")
        if node_id not in (f, t):
            continue
        # Always evaluate ACL on the neighbor node so allowed_ids reflect real visibility
        other = t if f == node_id else f
        other_doc = st.get_node(other) or {}
        # Explicit intra-domain rule for CAUSAL only (Baseline §5)
        if et in set(CAUSAL_EDGE_TYPES):
            if (other_doc.get("domain") and other_doc.get("domain") != (anchor_doc or {}).get("domain")):
                try:
                    log_stage(logger, "edges_scope", "edge_dropped_domain",
                              edge_type=et, anchor=node_id, other=other, request_id=request_id)
                except (RuntimeError, ValueError, TypeError):
                    pass
                continue
        allowed_n, _ = acl_check(other_doc, policy)
        if not allowed_n:
            try:
                log_stage(logger, "edges_scope", "edge_dropped_acl",
                          edge_type=et, anchor=node_id, other=other, request_id=request_id)
            except (RuntimeError, ValueError, TypeError):
                pass
            continue
        ed = {"type": et, "from": f, "to": t, "timestamp": (e or {}).get("timestamp")}
        dom = (e or {}).get("domain")
        if dom:
            ed["domain"] = dom
        edges_kept.append(ed)

    # Bounded alias tails per Baseline: inbound ALIAS_OF (event→anchor) then one-hop decisions in that event's domain
    anchor_id_storage = node_id
    alias_event_ids: list[str] = []
    for e in edges_in:
        if (e or {}).get("type") not in set(ALIAS_EDGE_TYPES):
            continue
        if (e or {}).get("to") != anchor_id_storage:
            continue
        ev_id = (e or {}).get("from")
        if not ev_id:
            continue
        # ACL on the alias event itself
        ev_doc = st.get_node(ev_id) or {}
        allowed_ev, _ = acl_check(ev_doc, policy)
        if not allowed_ev:
            continue
        alias_event_ids.append(ev_id)
    alias_edges_to_add: list[dict] = []
    for ev_id in alias_event_ids:
        # Always derive the alias anchor from the storage key.  If conversion fails,
        # fall back to the raw ev_id.  This avoids duplicating the domain when the
       # node id is already a storage key.
        try:
            alias_anchor = storage_key_to_anchor(ev_id)
        except (ValueError, TypeError, AttributeError):
            alias_anchor = str(ev_id)

        # Fetch up to 3 decisions following this event in its domain
        try:
            decisions = st.next_decisions_from_event(ev_id, limit=3) or []
        except (RuntimeError, OSError):
            decisions = []
        for d in decisions[:3]:
            # next_decisions_from_event returns the decision’s storage key in d["id"]
            other_id = (d or {}).get("id")
            dec_anchor: Optional[str] = None
            if other_id:
                try:
                    dec_anchor = storage_key_to_anchor(other_id)
                except (ValueError, TypeError, AttributeError):
                    dec_domain = (d or {}).get("domain")
                    dec_id = other_id
                    if dec_domain and dec_id:
                        dec_anchor = f"{dec_domain}#{dec_id}"
                    else:
                        dec_anchor = str(other_id)
            edge = (d or {}).get("edge") or {}
            try:
                from core_models.ontology import canonical_edge_type
                et = canonical_edge_type(edge.get("type"))
            except (ValueError, TypeError):
                et = str(edge.get("type") or "")
            ts = edge.get("timestamp")
            # Only include alias-tail edges for causal types (LED_TO, CAUSAL)
            if et in set(CAUSAL_EDGE_TYPES) and dec_anchor and ts:
                alias_edges_to_add.append(
                    {"type": et, "from": alias_anchor, "to": dec_anchor, "timestamp": ts}
                )
    if alias_edges_to_add:
        edges_kept.extend(alias_edges_to_add)
        try:
            log_stage(logger, "alias_tail", "added",
                      added_count=len(alias_edges_to_add), request_id=request_id)
        except (ValueError, TypeError, RuntimeError):
            pass
    return edges_kept


# --------------- Enrichment -------------
@app.get("/api/enrich")
async def enrich(anchor: str, response: Response, request: Request):
    """
    Type-agnostic enrich: lookup by anchor (Decision, Event, future types).
    """
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    # Fail-closed policy headers (centralized)
    policy = _policy_from_request_headers(request.headers)
    # Validate anchor on the wire
    if not anchor or not is_valid_anchor(anchor):
        raise HTTPException(status_code=400, detail="invalid anchor (expected '<domain>#<id>')")
    key = anchor_to_storage_key(anchor)
    # Blocking store call → thread
    def _work() -> Optional[dict]:
        return store().get_enriched_node(key)
    try:
        with trace_span("memory.enrich", anchor=anchor):
            budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout")
    if doc is None:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        etag = store().get_snapshot_etag()
    except (RuntimeError, OSError, AttributeError):
        etag = None
    safe_etag = etag or "unknown"
    # Audit & attach snapshot meta
    if isinstance(doc, dict):
        doc = _attach_snapshot_meta(doc, etag)
    # Domain required + must match anchor’s domain (fail-closed)
    anchor_domain = (anchor.split("#", 1)[0] or "").strip()
    node_domain = (doc or {}).get("domain")
    if not node_domain or (anchor_domain and node_domain != anchor_domain):
        log_stage(logger, "enrich", "domain_mismatch_or_missing",
                  anchor=anchor, node_domain=node_domain, request_id=rid)
        raise HTTPException(status_code=int(policy.get("denied_status") or 403),
                            detail="acl:domain_mismatch_or_missing")
    # ACL then mask
    allowed, reason = acl_check(doc, policy)
    if not allowed:
        log_stage(
            logger, "enrich", "acl_denied",
            anchor=anchor,
            reason=reason,
            domain=(doc or {}).get("domain", ""),
            scopes=policy.get("domain_scopes"),
            request_id=rid,
        )
        raise HTTPException(status_code=403, detail=reason or "acl:denied")
    # Avoid inferring a type when missing.  Base document is copied verbatim; type
    # inference is handled by the policy/schema.  See Baseline §1.1.
    base = dict(doc)
    masked, mask_summary = field_mask_with_summary(base, policy)
    return _json_response_with_etag({"mask_summary": mask_summary, **masked}, safe_etag)

@app.head("/api/enrich")
async def enrich_head(anchor: str, response: Response, request: Request):
    """
    Cheap freshness check for Gateway: returns only ETag/x-snapshot-etag.
    No read-time normalization; no body.
    """
    etag = store().get_snapshot_etag() if store() else None
    safe_etag = etag or "unknown"
    inm = request.headers.get("if-none-match") or request.headers.get("If-None-Match")
    # Reflect ETag headers (quote per RFC)
    response.headers["ETag"] = f"\"{safe_etag}\""
    response.headers["x-snapshot-etag"] = safe_etag
    if inm and inm.strip("\"") == safe_etag:
        return Response(status_code=304)
    return Response(status_code=200)

@app.get("/api/enrich/event")
async def enrich_event(anchor: str, response: Response, request: Request):
    """
    Return a fully enriched event document.  The synchronous store call is offloaded
    to a worker thread with a configurable timeout to avoid blocking the event loop.
    Missing events return 404 and timeouts return 504.
    """
    # Fail-closed: centralized policy parsing
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    policy = _policy_from_request_headers(request.headers)
    # Resolve anchor to storage key (was missing; caused NameError on node_id)
    node_id = anchor_to_storage_key(anchor)
    with trace_span("memory.enrich_event", node_id=node_id):
        def _work() -> Optional[dict]:
            st = store()
            return st.get_enriched_event(node_id)
        try:
            budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="timeout")
        if doc is None:
            raise HTTPException(status_code=404, detail="event_not_found")
        try:
            etag = store().get_snapshot_etag()
        except (RuntimeError, OSError, AttributeError):
            etag = None
        safe_etag = etag or "unknown"
    if isinstance(doc, dict):
        doc = _attach_snapshot_meta(doc, etag)
    # ---- Policy-aware masking + ACL guard (parity with decision) -------------
    allowed, reason = acl_check(doc, policy)
    if not allowed:
        log_stage(logger, "enrich", "acl_denied",
                  node_type="event", node_id=node_id, reason=reason, request_id=rid)
        raise HTTPException(status_code=403, detail=reason or "acl:denied")
    # Avoid forcing the type to EVENT.  Copy the document as-is and apply field masking.
    base = dict(doc)
    masked, mask_summary = field_mask_with_summary(base, policy)
    result = {"mask_summary": mask_summary, "event": masked}
    return _json_response_with_etag(result, safe_etag)

# --------------- Batch Enrichment (bounded, policy- & snapshot-bound) -------------
@app.post("/api/enrich/batch")
async def enrich_batch(payload: dict, response: Response, request: Request):
    """
    Enrich a bounded set of node IDs for short-answer composition.
    Contract (Baseline v3, snapshot-bound):
      - Input: {"anchor_id": "<domain#id>", "snapshot_etag": "<etag>", "ids": ["..."]}
      - Policy: honour the same policy headers as /api/enrich; ACL-guard each item.
      - Scope safety: Memory recomputes the authorized set from the snapshot_etag/policy and
        **denies the whole call** if `requested_ids ⊄ allowed_ids` (default 403; optional 404 via x-denied-status).
      - Precondition: snapshot_etag is REQUIRED (body or X-Snapshot-ETag); missing/mismatch → 412.
      - Output on success: {"items": {"<id>": {...masked enriched node...}, ...}} with minimal meta.
    """
    # Fail-closed policy headers (centralized)
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    try:
        policy = _policy_from_request_headers(request.headers)
    except HTTPException:
        log_stage(logger, "policy", "header_error", request_id=(payload or {}).get("request_id"))
        raise
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    anchor_wire = str((payload.get("anchor_id") or "")).strip()
    if not anchor_wire or not is_valid_anchor(anchor_wire):
        raise HTTPException(status_code=400, detail="invalid anchor (expected '<domain>#<id>')")
    wanted_ids = [str(i).strip() for i in (payload.get("ids") or []) if isinstance(i, (str,))]

    # Snapshot precondition
    etag_now = (store().get_snapshot_etag() or "") if store() else ""
    precond = str((
        payload.get("snapshot_etag")
        or request.headers.get("X-Snapshot-ETag")
        or request.headers.get("X-Snapshot-Etag")
        or request.headers.get("x-snapshot-etag")
        or ""
    )).strip()
    # Baseline v3: precondition is REQUIRED. Missing OR mismatched -> 412.
    if (not precond) or (not etag_now) or (precond != etag_now):
        log_stage(logger, "enrich_batch", "precondition_failed",
                  provided=(precond or "<missing>"), current=(etag_now or "<unknown>"),
                  request_id=rid)
        raise HTTPException(status_code=412, detail="precondition:snapshot_etag_mismatch")

    # Recompute scope deterministically (same as expand_candidates)
    node_id = anchor_to_storage_key(anchor_wire)
    try:
        st = store()
        edges_adjacent = st.get_edges_adjacent(node_id)
        anchor_doc = st.get_node(node_id) or {}
    except (RuntimeError, OSError, AttributeError) as e:
        log_stage(logger, "enrich_batch", "store_unavailable",
                  error=type(e).__name__, request_id=rid)
        raise HTTPException(status_code=503, detail="store_unavailable")

    # 🔐 Same anchor ACL semantics as /api/enrich and /api/graph/expand_candidates
    allowed_anchor, reason = acl_check(anchor_doc or {}, policy)
    if not allowed_anchor:
        log_stage(logger, "enrich_batch", "acl_denied_anchor",
                  anchor=anchor_wire, reason=reason)
        raise HTTPException(status_code=int(policy.get("denied_status") or 403),
                            detail=reason or "acl:denied")
    # Domain required + must match anchor’s domain (fail-closed)
    anchor_domain = (anchor_wire.split("#", 1)[0] or "").strip()
    if not (anchor_doc or {}).get("domain") or (anchor_domain and (anchor_doc or {}).get("domain") != anchor_domain):
        log_stage(logger, "enrich_batch", "domain_mismatch_or_missing",
                  anchor=anchor_wire, node_domain=(anchor_doc or {}).get("domain"))
        raise HTTPException(status_code=int(policy.get("denied_status") or 403),
                            detail="acl:domain_mismatch_or_missing")

    # Recompute scope deterministically (k=1 + bounded alias tail) using shared helper
    edges_kept = _edges_with_acl_and_alias_tail(
        st, node_id, anchor_doc, policy, request_id=rid
    )
    # Use the same SoT for shaping/dedup before computing allowed_ids to avoid drift
    _edges_wire_for_ids: List[dict] = to_wire_edges(edges_kept)
    try:
        rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
        log_stage(logger, "view", "wire_edges_batch",
                  count_in=len(edges_kept), count_out=len(_edges_wire_for_ids),
                  request_id=rid)
    except (RuntimeError, ValueError, TypeError, KeyError):
        pass

    # --- OPA/Rego externalized decision (fallback to deterministic local computation) ---
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=anchor_wire,
        edges=_edges_wire_for_ids,
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=etag_now,
    )
    if opa_decision and opa_decision.allowed_ids:
        allowed_wire_ids = opa_decision.allowed_ids
        # Surface engine-chosen policy_fp & extra_visible to downstream masking
        try:
            policy["policy_fp"] = opa_decision.policy_fp  # prefer engine fingerprint
            if opa_decision.extra_visible:
                policy["extra_visible"] = opa_decision.extra_visible
        except Exception:
            log_once(logger, "opa_apply_policy_metadata_failed", level="warning")
    else:
        allowed_wire_ids = _compute_allowed_ids({"id": anchor_wire}, _edges_wire_for_ids)

    # Intersect deterministically and enforce policy per node
    out: dict = {}
    requested = set(wanted_ids)
    allowed_set = set(allowed_wire_ids)
    ids_to_fetch = sorted(requested & allowed_set)
    denied = sorted(requested - allowed_set)
    try:
        log_stage(
            logger, "enrich_batch", "ids_clamped",
            requested=len(requested),
            allowed=len(allowed_set),
            will_fetch=len(ids_to_fetch),
            denied=len(denied),
            anchor=anchor_wire,
            policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
            snapshot_etag=etag_now or "unknown",
            request_id=rid,
        )
    except (RuntimeError, ValueError, TypeError, KeyError):
        pass

    # If the client requested any disallowed ids → fail-closed (no partials)
    if denied:
        log_stage(logger, "enrich_batch", "requested_ids_out_of_scope",
                  denied_count=len(denied), anchor=anchor_wire, request_id=rid)
        raise HTTPException(status_code=int(policy.get("denied_status") or 403),
                            detail="acl:requested_ids_out_of_scope")

    for wid in ids_to_fetch:
        try:
            storage_key = anchor_to_storage_key(wid)
        except Exception:
            continue
        doc = None
        try:
            doc = store().get_enriched_node(storage_key)
        except Exception:
            doc = None
        if not isinstance(doc, dict):
            continue
        allowed, _reason = acl_check(doc, policy)
        if not allowed:
            continue
        masked, mask_summary = field_mask_with_summary(dict(doc), policy)
        masked["mask_summary"] = mask_summary
        out[wid] = masked

    if etag_now:
        response.headers["x-snapshot-etag"] = etag_now
    # Emit fingerprints in meta for FE cache keys (and mirrors in headers for audit drawers)
    response.headers["X-BV-Policy-Fingerprint"] = str(policy.get("policy_fp") or "")
    meta_allowed_ids_fp = fp_allowed_ids(allowed_wire_ids)
    response.headers["X-BV-Allowed-Ids-FP"] = meta_allowed_ids_fp
    # Surface compact policy meta for FE audit drawers (parity across routes)
    try:
        _extra = policy.get("extra_visible") if isinstance(policy, dict) else None
        # keep small & opaque; FE knows how to parse JSON if present
        if _extra:
            import orjson as _orjson  # local import to avoid global dep if unused
            response.headers["X-BV-Policy-Meta"] = _orjson.dumps(
                {"extra_visible": _extra}
            ).decode("utf-8")
    except Exception:
        # do not fail response on header encoding issues
        pass
    # Trim meta to *returned_count* (no totals). Keep allowlist/fps for FE/cache determinism.
    return {
        "items": out,
        "meta": {
            "returned_count": len(out),
            "allowed_ids": allowed_wire_ids,
            "allowed_ids_fp": meta_allowed_ids_fp,
            "policy_fp": str(policy.get("policy_fp") or "unknown"),
            "snapshot_etag": etag_now,
        },
    }

# --------------- Resolver ------------------
@app.post("/api/resolve/text")
async def resolve_text(payload: dict, response: Response, request: Request):
    # Hot path MUST NOT invalidate storage connection/cache.
    # Tests can explicitly call _clear_store_cache() via a test-only hook.
    _timers = _StageTimers()
    _timers.start('resolve')
    _any_cache_hit = False
    q = payload.get("q", "")
    # --- M5 cache (resolve) ---
    try:
        policy = compute_effective_policy({k: v for k, v in request.headers.items()})
    except PolicyHeaderError as e:
        # Fail-closed with explicit error class for auditability (Baseline §6).
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")
    except (ValueError, KeyError) as e:
        # Narrow failures (bad/missing header values).
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")
    try:
        etag_for_cache = store().get_snapshot_etag()
    except Exception:
        etag_for_cache = None
    cache_key = None
    if etag_for_cache and q:
        cache_key = cache_keys.mem_resolve(etag_for_cache or "unknown", policy.get("policy_fp") or "", q)
        cached = None
        redis_client = None
        try:
            redis_client = get_redis_pool()
        except (AttributeError, RuntimeError, OSError):
            redis_client = None
        if redis_client is not None and cache_key:
            try:
                rc = RedisCache(redis_client)
                log_stage(logger, "cache", "get", layer="resolve", cache_key=cache_key)
                raw = await rc.get(cache_key)
                if raw:
                    try:
                        cached = jsonx.loads(raw)
                    except (ValueError, TypeError):
                        cached = None
            except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
                cached = None
        if cached:
            _any_cache_hit = True
            log_stage(logger, "cache", "hit", layer="resolve", cache_key=cache_key)
            try:
                meta = (cached.get("meta") or {})
                fps  = meta.get("fingerprints") or {}
                pfp  = meta.get("policy_fp") or fps.get("policy_fingerprint")
                if pfp:
                    response.headers["X-BV-Policy-Fingerprint"] = pfp
            except (TypeError, KeyError, AttributeError, ValueError):
                pass
            return _json_response_with_etag(_attach_snapshot_meta(cached, etag_for_cache), etag_for_cache)
        else:
            log_stage(logger, "cache", "miss", layer="resolve", cache_key=cache_key)
    # Vector mode is opt-in only: honour explicit payload flags; no auto-embedding.
    _use_vector_raw = payload.get("use_vector", None)
    use_vector = bool(_use_vector_raw)
    query_vector = payload.get("query_vector")
    if not q and not (use_vector and query_vector):
        return {"matches": [], "query": q, "vector_used": False}
    if q and is_valid_anchor(q):
        try:
            node = store().get_node(anchor_to_storage_key(q))
        except Exception:
            node = None
        if node:
            doc = {
                "query": q,
                "matches": [
                    {
                        "id": q,  # always echo the anchor on the wire
                        "score": 1.0,
                        "title": node.get("title") or node.get("option"),
                        "type": node.get("type"),
                    }
                ],
                "vector_used": False,
                # 🔑  Contract: resolved_id must always be present & non-null
                "resolved_id": q,
            }
            try:
                etag = store().get_snapshot_etag()
            except Exception:
                etag = None
            safe_etag = etag or "unknown"
            try:
                _timers.stop('resolve')
                doc.setdefault('meta', {}).setdefault('runtime', {})['stage_latencies_ms'] = _timers.as_dict()
                doc['meta']['runtime']['cache_hit'] = bool(_any_cache_hit)
                try:
                    doc['meta']['runtime']['timeout_ms'] = int(1000 * timeout_for_stage("search"))
                except (ValueError, TypeError):
                    pass
                for _k, _v in _timers.as_dict().items():
                    try:
                        log_stage(
                            logger, _k, 'stage_latency',
                            ms=int(_v),
                            request_id=(payload.get('request_id') if isinstance(payload, dict) else None),
                            snapshot_etag=safe_etag,
                        )
                    except (ValueError, TypeError, RuntimeError):
                        pass
            except (ValueError, TypeError, RuntimeError, KeyError):
                pass
            return _json_response_with_etag(doc, safe_etag)
    # IMPORTANT: to_thread expects a *sync* callable
    def _work():
        # create store inside the worker to avoid eager connection
        st = store()
        return st.resolve_text(
            q,
            limit=int(payload.get("limit", 10)),
            use_vector=use_vector,
            query_vector=query_vector,
        )
    try:
        with trace_span("memory.resolve_text", q=q, use_vector=use_vector):
            # enforce 0.8s timeout, as per spec
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("search"))
    except asyncio.TimeoutError:
        log_stage(logger, "resolve", "timeout", request_id=payload.get("request_id"))
        raise HTTPException(status_code=504, detail="timeout")
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        # Unit-test friendly fallback: return empty contract (no DB required)
        log_stage(logger, "resolver", "fallback_empty",
                  error=type(e).__name__, request_id=payload.get("request_id"))
        doc = {"query": q, "meta": {"fallback_reason": "db_unavailable"}}
    try:
        etag = store().get_snapshot_etag()
    except (RuntimeError, OSError, AttributeError):
        etag = None
    if etag:
        response.headers["x-snapshot-etag"] = etag
    # Also surface the snapshot in the body for simpler audit drawers
    doc.setdefault("meta", {})
    doc["meta"]["snapshot_etag"] = etag or "unknown"
    doc["meta"]["snapshot_available"] = bool((etag or "") and (etag or "") != "unknown")
    # Convenience flag: was vector search even available?
    doc["meta"]["vector_enabled"] = (os.getenv("ENABLE_EMBEDDINGS", "").lower() == "true")
    # Ensure contract keys present (normalize to input)
    # ---- 🔒 Contract normalisation (Milestone-2) ---- #
    doc["query"] = q                         # echo the raw query back
    doc.setdefault("matches", [])            # always a list
    doc.setdefault("vector_used", bool(use_vector))
    doc.setdefault("meta", {})

    # ---------- ensure non-null resolved_id ------------------------------ #
    if doc.get("matches"):
        doc["resolved_id"] = doc["matches"][0].get("id")
    else:
        doc["resolved_id"] = q
    # Attach runtime timings & cache flag before returning
    try:
        _timers.stop('resolve')
        doc.setdefault('meta', {}).setdefault('runtime', {})['stage_latencies_ms'] = _timers.as_dict()
        doc['meta']['runtime']['cache_hit'] = bool(_any_cache_hit)
        try:
            doc['meta']['runtime']['timeout_ms'] = int(1000 * timeout_for_stage("search"))
        except Exception:
            pass
    except Exception:
        pass
    # - Uses shared Redis client + wrapper
    # - Key: cache_keys.mem_resolve(snapshot_etag, policy_fp, query)
    # - TTL: TTL_EVIDENCE_CACHE_SEC
    # - Logs: cache_store with layer="resolve" and ttl
    if cache_key and isinstance(doc, dict):
        try:
            rc = RedisCache(get_redis_pool())
            await rc.setex(cache_key, int(TTL_EVIDENCE_CACHE_SEC), jsonx.dumps(doc))
            log_stage(logger, "cache", "store", layer="resolve",
                      cache_key=cache_key, ttl=int(TTL_EVIDENCE_CACHE_SEC))
        except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
            # best-effort; avoid raising on cache write
            pass
        except Exception:
                # swallow Redis errors silently (deterministic fallback)
                pass
        except Exception:
            pass
    matches = doc.get("matches", []) or []
    n = len(matches)
    result_flag = "0" if n == 0 else ("1" if n == 1 else "n")
    return _json_response_with_etag(doc, etag)

@app.post("/api/graph/expand_candidates")
async def expand_candidates(payload: dict, response: Response, request: Request):
    _timers = _StageTimers()

    # Ensure *etag* is always bound, even when the store raises and we
    # fall back to a dummy-document.
    etag: Optional[str] = None
    # Anchors only on the wire
    anchor = (payload or {}).get("anchor")
    if not anchor or not is_valid_anchor(anchor):
        log_stage(logger, "expand", "input_error",
                  reason="invalid_anchor_format", expected="<domain>#<id>", anchor=anchor)
        raise HTTPException(status_code=400, detail="invalid anchor (expected '<domain>#<id>')")
    node_id = anchor_to_storage_key(anchor)
    # Always bound to 1 for the demo (policy may request lower).
    k = 1
    # Resolve effective policy BEFORE storage to avoid referencing undefined policy in worker.
    try:
        policy = compute_effective_policy({k: v for k, v in request.headers.items()})
    except PolicyHeaderError as e:
        # Narrow, structured error for auditability
        log_stage(
            logger, "policy", "header_error",
            request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
        )
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")
    except (ValueError, KeyError) as e:
        log_stage(
            logger, "policy", "header_error",
            request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
        )
        raise HTTPException(status_code=400, detail=f"policy_error:{type(e).__name__}")
    # Snapshot etag for cache keys
    try:
        _st_for_etag = store()
        safe_etag = _st_for_etag.get_snapshot_etag() or "unknown"
    except Exception:
        _st_for_etag = None
        safe_etag = "unknown"

    # ---- M5 cache (expand) : bv:mem:v1:expand:{fp(etag,policy_fp,anchor)} ----
    from core_cache import keys as cache_keys  # local import to avoid import churn on startup
    from core_cache.redis_cache import RedisCache
    from core_cache.redis_client import get_redis_pool
    from core_config.constants import TTL_EVIDENCE_CACHE_SEC
    cache_key = cache_keys.mem_expand_candidates(
        safe_etag, str(policy.get("policy_fp") or ""), anchor
    )
    try:
        rc = RedisCache(get_redis_pool())
        raw = await rc.get(cache_key)
        cached = None
        if raw:
            try:
                cached = jsonx.loads(raw)
            except (ValueError, TypeError):
                cached = None
            if isinstance(cached, dict):
                log_stage(logger, "cache", "hit", layer="expand", cache_key=cache_key)
                # mirror what resolve_text does: propagate policy & snapshot in headers
                response.headers["x-snapshot-etag"] = safe_etag
                response.headers["X-BV-Policy-Fingerprint"] = str(policy.get("policy_fp") or "")
                return _json_response_with_etag(cached, safe_etag)
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
        # best-effort; fall through on cache errors
        pass
    log_stage(logger, "cache", "miss", layer="expand", cache_key=cache_key)

    # Storage fetch: edges adjacent to anchor + snapshot etag
    def _work():
        st = store()
        return st.get_edges_adjacent(node_id), st.get_snapshot_etag(), st
    # Fetch edges directly without using legacy expand/masked caches.
    _timers.start('expand')
    try:
        with trace_span("memory.expand_candidates", anchor=anchor, k=k):
            doc, etag, st = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("expand"))
    except asyncio.TimeoutError:
        # Honour timeout semantics by returning an empty candidate set on timeout.
        log_stage(
            logger, "expand", "timeout_fallback",
            request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
            snapshot_etag=safe_etag,
            policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
        )
        doc, st = {
            "node_id": node_id, "edges": [], "meta": {"snapshot_etag": safe_etag}
        }, store()
    except (ConnectionError, RuntimeError, ValueError) as e:
        # Defensive guardrail: on connection errors or other runtime issues, return an empty candidate set.
        log_stage(
            logger, "expand", "fallback_empty",
            error=type(e).__name__,
            request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
            snapshot_etag=safe_etag,
            policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
        )
        doc, st = {
            "node_id": node_id, "edges": [], "meta": {"snapshot_etag": safe_etag}
        }, store()
    finally:
        # Record expand timing to Prom regardless of outcome
        try:
            _timers.stop('expand')
            _ms = int((_timers.as_dict() or {}).get("expand", 0))
            metric_histogram("memory_stage_expand_seconds", float(_ms) / 1000.0)
        except Exception:
            pass
    # Normalise contract keys from storage
    result = {"node_id": (doc.get("node_id") or doc.get("anchor")), "edges": list(doc.get("edges") or []), "meta": dict(doc.get("meta") or {})}

    # Determine whether an alias-follow changed the anchor (0/1 for dashboards)
    alias_followed = 1 if (result.get("node_id") and result["node_id"] != node_id) else 0
    
    # -------------------------
    # 🔐 Policy Pre-Selector (edges-only)
    # -------------------------
    log_stage(
        logger, "policy", "resolved",
        role=policy.get("role"),
        namespaces=",".join(policy.get("namespaces") or []),
        scopes=",".join(policy.get("domain_scopes") or []),
        edge_types=",".join(policy.get("edge_allowlist") or []),
        sensitivity=policy.get("sensitivity_ceiling"),
        policy_key=policy.get("policy_key"),
        policy_version=policy.get("policy_version"),
        policy_fp=policy.get("policy_fp"),
        user_id=policy.get("user_id"),
        request_id=policy.get("request_id"),
        trace_id=policy.get("trace_id"),
    )
    # Derive the final snapshot etag; if unknown, we skip caching
    safe_etag = (etag or (result.get("meta") or {}).get("snapshot_etag")) or "unknown"

    # (M5) Masked-cache already handled earlier; remove legacy preselector cache.

    # Mask the anchor document according to role visibility
    try:
        anchor_doc = st.get_node(result["node_id"])
    except Exception:
        anchor_doc = None
    # 🔐 Fail-closed: if caller isn't allowed to see the anchor, deny expansion early.
    allowed_anchor, reason = acl_check(anchor_doc or {}, policy)
    if not allowed_anchor:
        log_stage(
            logger, "expand", "acl_denied_anchor",
            anchor=anchor,
            reason=reason,
            policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
            domain=(anchor_doc or {}).get("domain",""),
            scopes=policy.get("domain_scopes"),
        )
        raise HTTPException(status_code=403, detail=reason or "acl:denied")
    # Compute the wire-format anchor id from the storage key…
    _wire_anchor_id = storage_key_to_anchor(result["node_id"]) if result.get("node_id") else ""
    # Copy the anchor doc and override id with the wire anchor
    _anchor_doc_copy = {} if not anchor_doc else dict(anchor_doc)
    _anchor_doc_copy["id"] = _wire_anchor_id or result["node_id"]
    # Require that the stored anchor has a domain and it matches wire anchor’s domain
    anchor_domain = (anchor.split("#", 1)[0] or "").strip()
    node_domain = (anchor_doc or {}).get("domain")
    if not node_domain or (anchor_domain and node_domain != anchor_domain):
        log_stage(logger, "expand", "domain_mismatch_or_missing",
                  anchor=anchor, node_domain=node_domain)
        raise HTTPException(status_code=int(policy.get("denied_status") or 403),
                            detail="acl:domain_mismatch_or_missing")
    masked_anchor = field_mask(_anchor_doc_copy or {"id": _wire_anchor_id or result["node_id"]}, policy)

    # Apply edge allowlist + ACL, then append bounded alias tails (shared helper)
    _timers.start('policy_mask')
    edges_kept = _edges_with_acl_and_alias_tail(st, result["node_id"], anchor_doc, policy)
    _timers.stop('policy_mask')

    # Canonical wire-view shaping & dedup (single source of truth in core_models)
    edges_kept_wire: List[dict] = to_wire_edges(edges_kept)
    # audit: counts only (no payloads)
    try:
        log_stage(logger, "view", "wire_edges", count_in=len(edges_kept), count_out=len(edges_kept_wire))
    except Exception:
        pass
    _edge_count = len(edges_kept_wire)

    # Derive alias meta STRICTLY from the edges we will return
    wire_anchor_id = (
        masked_anchor["id"]
        if isinstance(masked_anchor, dict) and isinstance(masked_anchor.get("id"), str)
        else None
    )
    # Canonical alias summary (single source of truth; prevents drift)
    alias_block = alias_meta(wire_anchor_id, edges_kept_wire)
    try:
        log_stage(logger, "alias", "returned_count", count=len(alias_block.get("returned", [])))
    except Exception:
        pass
    # Ensure alias meta has a well-formed 'returned' array (Baseline §3.2)
    if not isinstance(alias_block.get('returned'), list):
        alias_block['returned'] = []

    # --- OPA/Rego externalized decision (fallback to deterministic local computation) ---
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=_wire_anchor_id,
        edges=edges_kept_wire,
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=safe_etag,
    )
    if opa_decision and opa_decision.allowed_ids:
        _ids = opa_decision.allowed_ids
        # Adopt engine fingerprint for cache keys/audit
        policy["policy_fp"] = opa_decision.policy_fp
    else:
        _ids = _compute_allowed_ids({"id": _wire_anchor_id}, edges_kept_wire)

    # ── Assemble final wire Graph View (Baseline §3.2) ──────────────────────
    _timers.start('preselector')
    candidate_set = {
        "anchor": masked_anchor,
        "graph": {"edges": edges_kept_wire},
        "meta": {
            "snapshot_etag": safe_etag,
            "policy_fp": policy.get("policy_fp"),
            "fingerprints": { "graph_fp": fp_graph(wire_anchor_id, edges_kept_wire) },
            "alias": alias_block,
        },
    }
    # allowed_ids (WIRE) — non-optional (schema-required)
    candidate_set["meta"]["allowed_ids"] = _ids
    candidate_set["meta"]["allowed_ids_fp"] = fp_allowed_ids(_ids)
    candidate_set["meta"]["returned_count"] = len(_ids)
    try:
        log_stage(logger, "build_meta", "allowed_ids_set", count=len(_ids), sample=_ids[:3])
    except Exception:
        pass

    # Emit build_meta fingerprint log (observability §5.3)
    try:
        _meta = candidate_set.get("meta") or {}
        _fps = _meta.get("fingerprints") or {}
        log_stage(
            logger, "build_meta", "fingerprints",
            request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
            snapshot_etag=safe_etag,
            policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
            allowed_ids_fp=_meta.get("allowed_ids_fp"),
            graph_fp=_fps.get("graph_fp"),
        )
    except Exception:
        pass

    # Deterministic policy_fp logging (no legacy fallbacks; no try/except)
    _meta = candidate_set.get("meta") or {}
    _fp = _meta.get("policy_fp")
    if isinstance(_fp, str) and _fp:
        log_stage(logger, "policy", "policy_fp", computed_fp=_fp)
    _timers.stop('preselector')

    # Hidden counts log (audit-only; masked edges-only per Baseline)
    try:
        _raw_edges = (candidate_set.get('graph') or {}).get('edges') or []
        _edge_count2 = len(_raw_edges)
        _node_ids = set([ (candidate_set.get('anchor') or {}).get('id') ])
        for _e in _raw_edges:
            for _k in ('from', 'to'):
                _nid = (_e or {}).get(_k)
                if _nid:
                    _node_ids.add(_nid)
        log_stage(
            logger, 'audit', 'hidden_counts',
            request_id=(payload.get('request_id') if isinstance(payload, dict) else None),
            snapshot_etag=safe_etag,
            policy_fp=(policy.get('policy_fp') if isinstance(policy, dict) else None),
            allowed_ids_fp=((candidate_set.get('meta') or {}).get('allowed_ids_fp') if isinstance(candidate_set, dict) else None),
            nodes_considered=len(_node_ids),
            edges_considered=int(_edge_count2),
        )
    except Exception:
        pass
    # Validate outbound payload against schema (fail-closed)
    _ok, _errors = True, []
    # Contract guard: ETag must live in meta only
    if "snapshot_etag" in candidate_set:
        raise HTTPException(status_code=500, detail="shape_error:top_level_snapshot_etag")
    try:
        _res = validate_graph_view(candidate_set)
        if isinstance(_res, tuple) and len(_res) == 2:
            _ok, _errors = bool(_res[0]), list(_res[1] or [])
        else:
            _ok = True
    except Exception as e:
        _ok, _errors = False, [str(e)]
    if not _ok:
        try:
            _meta = (candidate_set.get("meta") if isinstance(candidate_set, dict) else {}) or {}
            _fps  = (_meta.get("fingerprints") or {})
            log_stage(
                logger, "validator", "graph_view_invalid",
                request_id=(policy.get("request_id") if isinstance(policy, dict) else None),
                snapshot_etag=safe_etag,
                policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
                allowed_ids_fp=_meta.get("allowed_ids_fp"),
                graph_fp=_fps.get("graph_fp"),
                error_count=len(_errors), sample_error=(_errors[0] if _errors else None),
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="validation_error:graph_view_invalid")

    # Write-through store (same TTL as resolve cache)
    try:
        rc = RedisCache(get_redis_pool())
        await rc.setex(cache_key, int(TTL_EVIDENCE_CACHE_SEC), jsonx.dumps(candidate_set))
        log_stage(logger, "cache", "store", layer="expand", cache_key=cache_key, ttl=int(TTL_EVIDENCE_CACHE_SEC))
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
        pass
    return _json_response_with_etag(candidate_set, safe_etag)

