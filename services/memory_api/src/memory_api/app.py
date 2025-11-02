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
from core_http.errors import attach_standard_error_handlers, raise_http_error
from core_logging.error_codes import ErrorCode
from core_observability.otel import inject_trace_context
from core_storage import ArangoStore
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_policy_opa import opa_decide_if_enabled, OPADecision
from core_logging import log_once
from core_models.ontology import is_valid_anchor
from core_utils.domain import anchor_to_storage_key, storage_key_to_anchor
from core_models.graph_view import to_wire_edges
from core_models.ontology import EDGE_TYPES, CAUSAL_EDGE_TYPES, ALIAS_EDGE_TYPES, canonical_edge_type
from core_utils import jsonx
from core_utils.graph import alias_meta, compute_allowed_ids
from core_validator import validate_graph_view
from core_utils.fingerprints import graph_fp as fp_graph, allowed_ids_fp as fp_allowed_ids, normalize_fingerprint
from core_cache import keys as cache_keys
from core_cache.redis_cache import RedisCache
from core_cache.redis_client import get_redis_pool
from core_http.client import get_http_client
from core_config.constants import timeout_for_stage, TTL_EVIDENCE_CACHE_SEC
from core_metrics import histogram as metric_histogram, counter as metric_counter
from .policy import compute_effective_policy, field_mask, field_mask_with_summary, acl_check, PolicyHeaderError
from core_http.headers import REQUEST_SNAPSHOT_ETAG, RESPONSE_SNAPSHOT_ETAG, BV_POLICY_FP, BV_ALLOWED_IDS_FP, BV_GRAPH_FP, BV_POLICY_ENGINE_FP, ETAG, IF_NONE_MATCH

settings = get_settings()
logger = get_logger("memory_api")

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
# Anchor helpers (single source of truth)
def _validate_anchor_or_400(anchor: str) -> str:
    """Ensure the given wire anchor has the canonical '<domain>#<id>' shape."""
    if not isinstance(anchor, str) or not anchor or not is_valid_anchor(anchor):
        raise HTTPException(status_code=400, detail="invalid anchor (expected '<domain>#<id>')")
    return anchor.strip()

# ────────────────────────────────────────────────────────────
# Domain invariants (single source of truth)
def _assert_anchor_domain(
    anchor: str,
    node_domain: str | None,
    *,
    request_id: str,
    status_code: int = 409,
    storage_node_id: str | None = None,
) -> None:
    """
    Fail-closed with precise error codes:
      - DOMAIN_MISSING  when storage returned a node without 'domain'
      - DOMAIN_MISMATCH when anchor.domain != node.domain
    """
    wire = _validate_anchor_or_400(anchor)
    a_dom = wire.split("#", 1)[0]
    if not node_domain:
        log_stage(
            logger, "expand", "domain_missing",
            request_id=request_id, anchor_id=wire, storage_node_id=(storage_node_id or "")
        )
        raise HTTPException(status_code=status_code, detail="acl:domain_missing")
    if a_dom != node_domain:
        log_stage(
            logger, "expand", "domain_mismatch",
            request_id=request_id, anchor_id=wire,
            anchor_domain=a_dom, node_domain=node_domain,
            storage_node_id=(storage_node_id or "")
        )
        raise HTTPException(status_code=status_code, detail="acl:domain_mismatch")

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

def _maybe_add_policy_advice_header(response: Response, request: Request, policy_fp: str) -> None:
    """
    If client provided X-Policy-Key and it doesn't match server-computed policy_fp,
    add an actionable hint for the client to use the canonical fingerprint.
    """
    provided = (request.headers.get("X-Policy-Key") or request.headers.get("x-policy-key") or "").strip()
    if provided and policy_fp and provided != str(policy_fp).strip():
        response.headers["X-Policy-Advice"] = f"Use X-Policy-Key: {policy_fp}"

def _log_policy_fp_pair(*, stage: str, request_id: str | None, local_fp: str | None, engine_fp: str | None) -> None:
    """
    One-line structured audit comparing local (header-derived) policy_fp vs engine (OPA) fp.
    Does not affect behavior; helps diagnose mixed-policy compositions.
    """
    if local_fp or engine_fp:
        log_stage(
            logger, stage, "policy.fp_pair",
            request_id=(request_id or ""),
            policy_fp_local=(str(local_fp) if local_fp else None),
            policy_fp_engine=(str(engine_fp) if engine_fp else None),
        )

def _clear_store_cache() -> None:  # pragma: no cover – trivial utility
    """Best-effort cache invalidation that tolerates monkey-patched *store*."""
    clear_fn = getattr(store, "cache_clear", None)  # type: ignore[attr-defined]
    if callable(clear_fn):
        clear_fn()

def _policy_from_request_headers(h: Mapping[str, str]) -> dict:
    """Centralized policy parsing with consistent error mapping + explicit logging."""
    try:
        return compute_effective_policy({k: v for k, v in h.items()})
    except PolicyHeaderError as e:
        # Log which headers are bad, if available on the exception
        log_stage(
            logger, "policy", "header_error",
            detail=str(e),
            missing=getattr(e, "missing", None),
            empty=getattr(e, "empty", None),
            invalid=getattr(e, "invalid", None),
        )
        # Return the detailed string (e.g., "missing_required_headers:missing=x-sensitivity-ceiling")
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, KeyError) as e:
        log_stage(logger, "policy", "header_error", detail=type(e).__name__)
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
    res = _ping_gateway_ready()
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

def _json_response_with_etag(payload: dict, etag: Optional[str] = None) -> JSONResponse:
    """
    Build a JSONResponse and, when available, mirror the repository’s current
    snapshot ETag in the `x-snapshot-etag` header so that gateways and tests
    can rely on cache-invalidation semantics.
    """
    resp = JSONResponse(content=payload)
    if etag:
        resp.headers[RESPONSE_SNAPSHOT_ETAG] = etag
    sfp = _schema_fp()
    if sfp:
        resp.headers["X-BV-Schema-FP"] = sfp
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            fps = meta.get("fingerprints")
            pfp = (meta.get("policy_fp"))
            if pfp:
                resp.headers[BV_POLICY_FP] = str(pfp)
            aid_fp = meta.get("allowed_ids_fp")
            if aid_fp:
                resp.headers[BV_ALLOWED_IDS_FP] = str(aid_fp)
            if isinstance(fps, dict):
                gfp = fps.get("graph_fp")
                # Only echo Graph-FP when the payload actually contains a 'graph' block
                if isinstance(gfp, str) and gfp and isinstance(payload.get('graph'), dict):
                    resp.headers[BV_GRAPH_FP] = gfp
                    # Observability: header adoption (once per request)
                    try:
                        log_once(logger, key=f"graph_fp_header_set:{gfp}",
                                 event="view.graph_fp_header_set", stage="view", graph_fp=gfp)
                        metric_counter('memory_view_graph_fp_header_set_total', 1)
                    except (TypeError, ValueError):
                        pass
    return resp

def _attach_snapshot_meta(doc: dict, etag: Optional[str]) -> dict:
    """Attach/normalize snapshot_etag inside doc.meta, if an etag is available.
    - No-op for non-dict docs (defensive).
    - Does not create meta unless there's a non-empty etag.
    - Idempotent when the existing value already matches.
    """
    if not isinstance(doc, dict):
        return doc
    et = (etag or "").strip() if isinstance(etag, str) else ""
    if not et:
        return doc
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    if meta.get("snapshot_etag") != et:
        meta["snapshot_etag"] = et
    doc["meta"] = meta
    return doc

# ──────────────────────────────────────────────────────────────────────────────
# Shared helper: strict snapshot precondition (412 on missing/mismatch)
# ──────────────────────────────────────────────────────────────────────────────
def _require_snapshot_precondition(
    request: Request,
    *,
    payload: Optional[Mapping[str, object]] = None,
    stage: str = "read",
) -> str:
    """Return the current snapshot etag if the precondition passes; otherwise 412."""
    try:
        _raw = store().get_snapshot_etag()
    except (RuntimeError, OSError, AttributeError):
        _raw = None
    # Normalize EXACTLY like HEAD /api/enrich so clients can use the sentinel
    # value before the first ingest.
    etag_now = (_raw or "unknown").strip()
    precond = ""
    if payload and isinstance(payload, Mapping):
        val = payload.get("snapshot_etag")
        if isinstance(val, str):
            precond = val.strip()
    if not precond:
        precond = str(request.headers.get(REQUEST_SNAPSHOT_ETAG) or "").strip()
    # Provide precise reasons while remaining fail-closed.
    if not etag_now:
        log_stage(logger, stage, "precondition_no_snapshot",
                  provided=(precond or "<missing>"), current="<none>")
        raise HTTPException(
            status_code=412,
            detail="precondition:no_snapshot",
            headers={RESPONSE_SNAPSHOT_ETAG: etag_now},
        )
    if not precond:
        log_stage(logger, stage, "precondition_missing",
                  provided="<missing>", current=etag_now)
        raise HTTPException(
            status_code=412,
            detail="precondition:missing",
            headers={RESPONSE_SNAPSHOT_ETAG: etag_now},
        )
    if precond != etag_now:
        log_stage(logger, stage, "precondition_mismatch",
                  provided=precond, current=etag_now)
        raise HTTPException(
            status_code=412,
            detail="precondition:snapshot_etag_mismatch",
            headers={RESPONSE_SNAPSHOT_ETAG: etag_now},
        )
    # Success: return the normalized snapshot etag for callers to mirror in meta & headers
    return etag_now

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
    # Normalize policy allowlist (accept CSV/list/synonyms)
    allowed_types = set()
    if isinstance(policy, dict):
        raw = policy.get("edge_allowlist")
        if isinstance(raw, str):
            tokens = [t for t in raw.split(",") if t]
        elif isinstance(raw, (list, tuple, set)):
            tokens = list(raw)
        else:
            tokens = list(EDGE_TYPES)
        allowed_types = {canonical_edge_type(t) for t in tokens}
    if not allowed_types:
        allowed_types = set(EDGE_TYPES)

    edges_in: list = (st.get_edges_adjacent(node_id) or {}).get("edges") or []
    edges_kept: list = []

    for e in edges_in:
        et = canonical_edge_type((e or {}).get("type"))
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
        try:
            et = canonical_edge_type((e or {}).get("type"))
        except ValueError:
            continue
        if et not in set(ALIAS_EDGE_TYPES):
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
            try:
                log_stage(logger, "policy", "id_normalized",
                          before=str(ev_id), after=str(alias_anchor), request_id=request_id)
            except (RuntimeError, ValueError, TypeError):
                pass
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
                    try:
                        log_stage(logger, "policy", "id_normalized",
                                  before=str(other_id), after=str(dec_anchor), request_id=request_id)
                    except (RuntimeError, ValueError, TypeError):
                        pass
                except (ValueError, TypeError, AttributeError):
                    dec_domain = (d or {}).get("domain")
                    dec_id = other_id
                    if dec_domain and dec_id:
                        dec_anchor = f"{dec_domain}#{dec_id}"
                    else:
                        dec_anchor = str(other_id)
            edge = (d or {}).get("edge") or {}
            try:
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
        # Enforce ACL on alias-tail decision targets before adding edges (prevents id-only leaks)
        guarded: list[dict] = []
        for ed in alias_edges_to_add:
            tgt_wire = ed.get("to")
            if not isinstance(tgt_wire, str):
                continue
            # Fetch the target doc by storage key (convert from anchor) and run ACL
            try:
                tgt_key = anchor_to_storage_key(tgt_wire)
            except (ValueError, TypeError, AttributeError):
                # If anchor is malformed, skip the edge
                try:
                    log_stage(logger, "alias_tail", "edge_dropped_malformed_anchor",
                              to=str(tgt_wire), request_id=request_id)
                except (RuntimeError, ValueError, TypeError):
                    pass
                continue
            try:
                tgt_doc = st.get_node(tgt_key) or {}
            except (RuntimeError, OSError):
                tgt_doc = {}
            ok, _reason = acl_check(tgt_doc, policy)
            if ok:
                guarded.append(ed)
            else:
                try:
                    log_stage(logger, "alias_tail", "edge_dropped_acl",
                              to=str(tgt_wire), request_id=request_id)
                except (RuntimeError, ValueError, TypeError):
                    pass
        edges_kept.extend(guarded)
        try:
            log_stage(logger, "alias_tail", "added",
                      added_count=len(guarded), request_id=request_id)
        except (ValueError, TypeError, RuntimeError):
            pass
    return edges_kept


# --------------- Enrichment -------------
@app.get("/api/enrich")
async def enrich(anchor: str, response: Response, request: Request):
    """Type-agnostic enrich: lookup by anchor (Decision, Event, future types).
    Snapshot policy: STRICT — requires X-Snapshot-ETag matching the current snapshot; missing/mismatch → 412.
    Headers: mirrors x-snapshot-etag and X-BV-Policy-Fingerprint; does NOT set X-BV-Graph-FP (no graph in the payload).
    Contract: returns a single masked node plus mask_summary.
    """
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    # Fail-closed policy headers (centralized)
    policy = _policy_from_request_headers(request.headers)
    # Validate anchor on the wire (single helper) and map to storage
    anchor = _validate_anchor_or_400(anchor)
    key = anchor_to_storage_key(anchor)
    # Strict snapshot precondition (enforce early to avoid wasted work)
    safe_etag = _require_snapshot_precondition(request, stage="enrich")
    # Blocking store call → thread
    def _work() -> Optional[dict]:
        return store().get_enriched_node(key)
    with trace_span("memory.enrich", anchor=anchor):
        try:
            budget_s = max(0.1, float(get_settings().timeout_enrich_ms) / 1000.0)
            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=budget_s)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="timeout")
    if doc is None:
        raise HTTPException(status_code=404, detail="not_found")
    # (precondition already enforced; safe_etag available)

    # Audit & attach snapshot meta
    if isinstance(doc, dict):
        doc = _attach_snapshot_meta(doc, safe_etag)
    # Domain required + must match anchor’s domain (fail-closed)
    _assert_anchor_domain(
        anchor,
        (doc or {}).get("domain"),
        request_id=rid,
        status_code=int(policy.get("denied_status") or 403),
        storage_node_id=key,
    )
    # --- OPA/Rego decision (parity with expand/enrich_batch) -----------------
    # Use engine decision for visibility/scopes; DO NOT override locally computed policy_fp.
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=anchor,
        edges=[],  # single-node enrich; no edges context
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=safe_etag,
        intents=["enrich"],
    )
    engine_fp: str | None = None
    if opa_decision:
        engine_fp = getattr(opa_decision, "policy_fp", None) or None
        if getattr(opa_decision, "denied_status", None):
            policy["denied_status"] = int(opa_decision.denied_status)
        explain = getattr(opa_decision, "explain", None)
        if isinstance(explain, dict):
            fv = (explain.get("field_visibility") or {})
            if fv:
                policy.setdefault("role_profile", {})["field_visibility"] = fv
        if getattr(opa_decision, "extra_visible", None):
            policy["extra_visible"] = opa_decision.extra_visible    

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
    # Normalize id to WIRE form to prevent "eng#eng_..." double-prefix anchors
    # (storage_key_to_anchor("eng_d-eng-010") → "eng#d-eng-010")
    wire_anchor = storage_key_to_anchor(key)
    base = dict(doc)
    base["id"] = wire_anchor  # single source of truth for downstream extras
    masked, mask_summary = field_mask_with_summary(base, policy)
    _resp = _json_response_with_etag({"mask_summary": mask_summary, **masked}, safe_etag)
    # Mirror policy fingerprint for audits (node-only response; no graph_fp)
    _pfp = policy.get("policy_fp") if isinstance(policy, dict) else None
    if isinstance(_pfp, str) and _pfp:
        _resp.headers[BV_POLICY_FP] = str(_pfp)
    if engine_fp:
        _resp.headers[BV_POLICY_ENGINE_FP] = str(engine_fp)
    _log_policy_fp_pair(stage="enrich", request_id=rid, local_fp=(_pfp if isinstance(_pfp, str) else None), engine_fp=engine_fp)
    _maybe_add_policy_advice_header(_resp, request, str(policy.get("policy_fp") or ""))
    return _resp

@app.head("/api/enrich")
async def enrich_head(anchor: str, request: Request):
    """
    Cheap freshness check: returns only ETag (quoted) and x-snapshot-etag; no body.
    No snapshot precondition required. Use If-None-Match with the quoted ETag to get 304.
    """
    _validate_anchor_or_400(anchor)
    etag = store().get_snapshot_etag() if store() else None
    safe_etag = etag or "unknown"
    inm = request.headers.get(IF_NONE_MATCH)
    # Mirror standard freshness headers only (HEAD returns no policy headers)
    headers = {
        ETAG: f"\"{safe_etag}\"",
        RESPONSE_SNAPSHOT_ETAG: safe_etag,
    }
    if isinstance(inm, str) and inm.strip("\"") == safe_etag:
        return Response(status_code=304, headers=headers)
    return Response(status_code=200, headers=headers)

@app.get("/api/enrich/event")
async def enrich_event(anchor: str, response: Response, request: Request):
    """Event-only enrich by anchor.
    Snapshot policy: STRICT — same as /api/enrich (requires X-Snapshot-ETag).
    Headers: mirrors x-snapshot-etag and X-BV-Policy-Fingerprint; does NOT set X-BV-Graph-FP.
    """
    # Fail-closed: centralized policy parsing
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    policy = _policy_from_request_headers(request.headers)
    # Validate wire anchor and map to storage key
    anchor = _validate_anchor_or_400(anchor)
    # Strict snapshot precondition: require X-Snapshot-ETag; 412 on missing/mismatch
    safe_etag = _require_snapshot_precondition(request, stage="enrich_event")
    from core_utils.domain import anchor_to_storage_key, storage_key_to_anchor, parse_anchor
    _, node_id = parse_anchor(anchor)  # keep if you need wire-vs-storage comparisons later
    key = anchor_to_storage_key(anchor)
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
    if isinstance(doc, dict):
        doc = _attach_snapshot_meta(doc, safe_etag)
    # Domain required + must match anchor’s domain (fail-closed, parity with /api/enrich)
    _assert_anchor_domain(
        anchor,
        (doc or {}).get("domain"),
        request_id=rid,
        status_code=int(policy.get("denied_status") or 403),
        storage_node_id=node_id,
    )

    # --- OPA/Rego decision (event parity) -----------------------------------
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=anchor,
        edges=[],  # event-only enrich
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=safe_etag,
        intents=["enrich_event"],
    )
    engine_fp: str | None = None
    if opa_decision:
        engine_fp = getattr(opa_decision, "policy_fp", None) or None
        if getattr(opa_decision, "denied_status", None):
            policy["denied_status"] = int(opa_decision.denied_status)
        explain = getattr(opa_decision, "explain", None)
        if isinstance(explain, dict):
            fv = (explain.get("field_visibility") or {})
            if fv:
                policy.setdefault("role_profile", {})["field_visibility"] = fv
        if getattr(opa_decision, "extra_visible", None):
            policy["extra_visible"] = opa_decision.extra_visible    

    # ---- Policy-aware masking + ACL guard (parity with decision) -------------
    allowed, reason = acl_check(doc, policy)
    if not allowed:
        log_stage(logger, "enrich", "acl_denied",
                  node_type="event", node_id=node_id, reason=reason, request_id=rid)
        raise HTTPException(status_code=403, detail=reason or "acl:denied")
    # Normalize id to WIRE form just like /api/enrich
    wire_anchor = storage_key_to_anchor(key)
    base = dict(doc)
    base["id"] = wire_anchor
    masked, mask_summary = field_mask_with_summary(base, policy)
    _resp = _json_response_with_etag({"mask_summary": mask_summary, "event": masked}, safe_etag)
    # Mirror policy fingerprint + advise if client sent a mismatched X-Policy-Key
    _pfp = policy.get("policy_fp") if isinstance(policy, dict) else None
    if isinstance(_pfp, str) and _pfp:
        _resp.headers[BV_POLICY_FP] = str(_pfp)
    if engine_fp:
        _resp.headers[BV_POLICY_ENGINE_FP] = str(engine_fp)
    _log_policy_fp_pair(stage="enrich_event", request_id=rid, local_fp=(_pfp if isinstance(_pfp, str) else None), engine_fp=engine_fp)
    _maybe_add_policy_advice_header(_resp, request, str(policy.get("policy_fp") or ""))
    return _resp

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
        log_stage(logger, "policy", "header_error", request_id=(request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or ""))
        raise
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    anchor_wire = _validate_anchor_or_400(str((payload.get("anchor_id") or "")).strip())
    wanted_ids = [str(i).strip() for i in (payload.get("ids") or []) if isinstance(i, (str,))]

    # Snapshot precondition (accept header or body), 412 on missing/mismatch
    etag_now = _require_snapshot_precondition(request, payload=payload, stage="enrich_batch")

    # Recompute scope deterministically (same as expand_candidates)
    from core_utils.domain import parse_anchor, anchor_to_storage_key, storage_key_to_anchor
    _, node_id = parse_anchor(anchor_wire)               # wire id (domain-less)
    key = anchor_to_storage_key(anchor_wire)             # storage key (domain_prefix)
    try:
        st = store()
        anchor_doc = st.get_node(key) or {}
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
    _assert_anchor_domain(
        anchor_wire,
        (anchor_doc or {}).get("domain"),
        request_id=rid,
        status_code=int(policy.get("denied_status") or 403),
        storage_node_id=key,
    )

    # Recompute scope deterministically (k=1 + bounded alias tail) using shared helper
    edges_kept = _edges_with_acl_and_alias_tail(
        st, key, anchor_doc, policy, request_id=rid
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

    # --- Compute deterministic local allowed_ids from the scoped edges (fail-closed) ---
    local_allowed_ids: List[str] = compute_allowed_ids({"id": anchor_wire}, _edges_wire_for_ids)
    # Start with local view by default; OPA may only reduce this via intersection.
    allowed_wire_ids: List[str] = sorted(set(local_allowed_ids))

    # --- OPA/Rego externalized decision (only narrows via intersection) ---
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=anchor_wire,
        edges=_edges_wire_for_ids,
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=etag_now,
        intents=["enrich_batch"],
    )
    engine_fp: str | None = None
    if opa_decision and opa_decision.allowed_ids:
        # Intersect OPA with our deterministic local set to avoid overexposure.
        opa_ids = set(opa_decision.allowed_ids)
        before_local = len(allowed_wire_ids)
        before_opa   = len(opa_ids)
        allowed_wire_ids = sorted(set(local_allowed_ids) & opa_ids)
        log_stage(
            logger, "enrich_batch", "allowed_ids_intersection",
            local=before_local, opa=before_opa, effective=len(allowed_wire_ids),
            anchor=anchor_wire
        )
        # Engine fingerprint only for telemetry; keep local policy_fp stable.
        engine_fp = getattr(opa_decision, "policy_fp", None) or None
        if opa_decision.denied_status is not None:
            policy["denied_status"] = int(opa_decision.denied_status)
        # OPA-driven field visibility for masking
        explain = getattr(opa_decision, "explain", None)
        if isinstance(explain, dict):
            fv = (explain.get("field_visibility") or {})
            if fv:
                policy.setdefault("role_profile", {})["field_visibility"] = fv
        if getattr(opa_decision, "extra_visible", None):
            policy["extra_visible"] = opa_decision.extra_visible

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
        except (ValueError, TypeError):
            continue
        doc = None
        try:
            doc = store().get_enriched_node(storage_key)
        except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
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
        response.headers[RESPONSE_SNAPSHOT_ETAG] = etag_now
    # Emit fingerprints in meta for FE cache keys (and mirrors in headers for audit drawers)
    response.headers[BV_POLICY_FP] = str(policy.get("policy_fp") or "")
    if engine_fp:
        response.headers[BV_POLICY_ENGINE_FP] = str(engine_fp)
    _log_policy_fp_pair(stage="enrich_batch", request_id=rid, local_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None), engine_fp=engine_fp)
    _maybe_add_policy_advice_header(response, request, str(policy.get("policy_fp") or ""))
    meta_allowed_ids_fp = fp_allowed_ids(allowed_wire_ids)
    response.headers[BV_ALLOWED_IDS_FP] = meta_allowed_ids_fp
    # Surface compact policy meta for FE audit drawers (parity across routes)
    try:
        _extra = policy.get("extra_visible") if isinstance(policy, dict) else None
        # keep small & opaque; FE knows how to parse JSON if present
        if _extra:
            import orjson as _orjson  # local import to avoid global dep if unused
            response.headers["X-BV-Policy-Meta"] = _orjson.dumps(
                {"extra_visible": _extra}
            ).decode("utf-8")
    except (RuntimeError, OSError, ValueError, TypeError):
        # do not fail response on header encoding issues
        pass
    # Trim meta to *returned_count* (no totals). Keep allowlist/fps for FE/cache determinism.
    return {
        "items": out,
        "meta": {
            "returned_count": len(out),
            "allowed_ids": allowed_wire_ids,
            "allowed_ids_fp": meta_allowed_ids_fp,
            "policy_fp": normalize_fingerprint(str(policy.get("policy_fp") or "")),
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
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
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
                res = _json_response_with_etag(_attach_snapshot_meta(cached, etag_for_cache), etag_for_cache)
                if pfp:
                    res.headers[BV_POLICY_FP] = pfp
                _maybe_add_policy_advice_header(res, request, str(policy.get("policy_fp") or ""))
                return res
            except (TypeError, KeyError, AttributeError, ValueError):
                # Fall back if cached meta is malformed
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
        q = _validate_anchor_or_400(q)
        try:
            node = store().get_node(anchor_to_storage_key(q))
        except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
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
            except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
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
        response.headers[RESPONSE_SNAPSHOT_ETAG] = etag
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
        except (ValueError, TypeError):
            pass
    except (ValueError, TypeError, RuntimeError, KeyError):
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
    matches = doc.get("matches", []) or []
    n = len(matches)
    result_flag = "0" if n == 0 else ("1" if n == 1 else "n")
    _resp = _json_response_with_etag(doc, etag)
    try:
        _resp.headers[BV_POLICY_FP] = str(policy.get("policy_fp") or "")
        _maybe_add_policy_advice_header(_resp, request, str(policy.get("policy_fp") or ""))
    except (TypeError, ValueError, AttributeError):
        pass
    return _resp

@app.post("/api/graph/expand_candidates")
async def expand_candidates(payload: dict, response: Response, request: Request):
    """Edges-only graph view around the anchor (k=1).
    Snapshot policy: STRICT — requires X-Snapshot-ETag (or `snapshot_etag` in body); missing/mismatch → 412. Mirrors x-snapshot-etag in responses for cache keys.
    Headers: sets X-BV-Graph-FP and mirrors X-BV-Policy-Fingerprint (graph fingerprint also present at meta.fingerprints.graph_fp).
    Contract: meta.fingerprints ONLY contains graph_fp; bundle_fp is never present here.
    """
    _timers = _StageTimers()

    # Ensure *etag* is always bound, even when the store raises and we
    # fall back to a dummy-document.
    etag: Optional[str] = None
    # Anchors only on the wire
    anchor = (payload or {}).get("anchor")
    anchor = _validate_anchor_or_400(anchor)
    from core_utils.domain import parse_anchor
    _, node_id = parse_anchor(anchor)
    # storage key (eng#d-eng-010 -> eng_d-eng-010)
    key = anchor_to_storage_key(anchor)
    # Always bound to 1 for the demo (policy may request lower).
    k = 1
    # Resolve effective policy BEFORE storage to avoid referencing undefined policy in worker.
    policy = _policy_from_request_headers(request.headers)
    # Stable request id for structured logs
    rid = (request.headers.get("x-request-id") or request.headers.get("X-Request-Id") or "")
    # Strict snapshot precondition (body `snapshot_etag` OR `X-Snapshot-ETag`)
    safe_etag = _require_snapshot_precondition(request, payload=payload, stage="expand")

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
                # Heal meta for older cached entries (ensure body mirrors headers)
                meta = (cached.get("meta") or {})
                if not isinstance(meta, dict):
                    meta = {}
                if not meta.get("snapshot_etag"):
                    meta["snapshot_etag"] = safe_etag
                if not meta.get("policy_fp"):
                    meta["policy_fp"] = normalize_fingerprint(str(policy.get("policy_fp") or ""))
                cached["meta"] = meta
                # Build the actual response, then attach headers to it.
                res = _json_response_with_etag(cached, safe_etag)  # sets x-snapshot-etag
                res.headers[BV_POLICY_FP] = str(policy.get("policy_fp") or "")
                _maybe_add_policy_advice_header(res, request, str(policy.get("policy_fp") or ""))
                return res
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
        # best-effort; fall through on cache errors
        pass
    log_stage(logger, "cache", "miss", layer="expand", cache_key=cache_key)

    # Storage fetch: edges adjacent to anchor + snapshot etag
    def _work():
        st = store()
        return st.get_edges_adjacent(key), st.get_snapshot_etag(), st
    # Fetch edges directly without using legacy expand/masked caches.
    _timers.start('expand')
    try:
        with trace_span("memory.expand_candidates", anchor=anchor, k=k):
            doc, etag, st = await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout_for_stage("expand"))
    except asyncio.TimeoutError:
        log_stage(logger, "expand", "timeout",
                  request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
                  snapshot_etag=safe_etag,
                  policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None))
        raise raise_http_error(504, ErrorCode.upstream_timeout, "expand_candidates timed out",
                               request_id=(payload.get("request_id") if isinstance(payload, dict) else None))
    except (ConnectionError, RuntimeError, ValueError) as e:
        log_stage(logger, "expand", "store_unavailable",
                  error=type(e).__name__,
                  request_id=(payload.get("request_id") if isinstance(payload, dict) else None),
                  snapshot_etag=safe_etag,
                  policy_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None))
        raise raise_http_error(503, ErrorCode.storage_unavailable, "storage unavailable",
                               request_id=(payload.get("request_id") if isinstance(payload, dict) else None))
    finally:
        # Record expand timing to Prom regardless of outcome
        try:
            _timers.stop('expand')
            _ms = int((_timers.as_dict() or {}).get("expand", 0))
            metric_histogram("memory_stage_expand_seconds", float(_ms) / 1000.0)
        except (RuntimeError, ValueError, TypeError):
            pass
    # Normalise contract keys from storage
    result = {"node_id": (doc.get("node_id") or doc.get("anchor")), "edges": list(doc.get("edges") or []), "meta": dict(doc.get("meta") or {})}

    # Determine whether an alias-follow changed the anchor (0/1 for dashboards)
    # result["node_id"] is a storage key; compare as wire IDs for accuracy
    alias_followed = 1 if (result.get("node_id")
                           and storage_key_to_anchor(result["node_id"]) != anchor) else 0
    # Observability: record the redirect explicitly (cheap + schema-safe)
    try:
        log_stage(logger, "alias", "followed", value=int(alias_followed))
        metric_counter("memory_alias_followed_total", int(alias_followed))
    except (RuntimeError, ValueError, TypeError, KeyError):
        # observability is best-effort; never fail the request on log/metric issues
        pass
    
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

    # Mask the anchor document according to role visibility
    try:
        anchor_doc = st.get_node(result["node_id"])
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
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
    if result.get("node_id"):
        try:
            log_stage(logger, "policy", "id_normalized",
                      before=str(result["node_id"]), after=str(_wire_anchor_id),
                      request_id=(payload.get("request_id") if isinstance(payload, dict) else None))
        except (RuntimeError, ValueError, TypeError):
            pass
    # Copy the anchor doc and override id with the wire anchor
    _anchor_doc_copy = {} if not anchor_doc else dict(anchor_doc)
    _anchor_doc_copy["id"] = _wire_anchor_id or result["node_id"]
    # Require that the stored anchor has a domain and it matches the wire anchor’s domain
    _assert_anchor_domain(
        anchor,
        (anchor_doc or {}).get("domain"),
        request_id=rid,
        status_code=int(policy.get("denied_status") or 403),
        # include storage key to make domain violations immediately traceable
        # in structured logs (parity with /api/enrich and /api/enrich/event)
        storage_node_id=result.get("node_id"),
    )
    masked_anchor = field_mask(_anchor_doc_copy or {"id": _wire_anchor_id or result["node_id"]}, policy)

    # Apply edge allowlist + ACL, then append bounded alias tails (shared helper)
    _timers.start('policy_mask')
    edges_kept = _edges_with_acl_and_alias_tail(st, result["node_id"], anchor_doc, policy)
    _timers.stop('policy_mask')

    # Canonical wire-view shaping & dedup (single source of truth in core_models)
    edges_kept_wire: List[dict] = to_wire_edges(edges_kept)
    # audit: counts only (no payloads)
    log_stage(logger, "view", "wire_edges", count_in=len(edges_kept), count_out=len(edges_kept_wire))

    # Derive alias meta STRICTLY from the edges we will return
    wire_anchor_id = (
        masked_anchor["id"]
        if isinstance(masked_anchor, dict) and isinstance(masked_anchor.get("id"), str)
        else None
    )
    # Canonical alias summary (single source of truth; prevents drift)
    alias_block = alias_meta(wire_anchor_id, edges_kept_wire)
    log_stage(logger, "alias", "returned_count", count=len(alias_block.get("returned", [])))
    # Ensure alias meta has a well-formed 'returned' array (Baseline §3.2)
    if not isinstance(alias_block.get('returned'), list):
        alias_block['returned'] = []

    # --- OPA/Rego externalized decision (fallback to deterministic local computation) ---
    opa_decision: OPADecision | None = opa_decide_if_enabled(
        anchor_id=_wire_anchor_id,
        edges=edges_kept_wire,
        headers={k: v for k, v in request.headers.items()},
        snapshot_etag=safe_etag,
        intents=["expand_candidates"],
    )
    engine_fp: str | None = None
    if opa_decision and opa_decision.allowed_ids:
        _ids = opa_decision.allowed_ids
        # Keep engine fingerprint for telemetry only; do NOT override local policy_fp
        engine_fp = getattr(opa_decision, "policy_fp", None) or None
        # OPA-driven field visibility for masking
        if getattr(opa_decision, "explain", None):
            fv = (opa_decision.explain or {}).get("field_visibility") or {}
            policy.setdefault("role_profile", {})["field_visibility"] = fv
    else:
        _ids = compute_allowed_ids({"id": _wire_anchor_id}, edges_kept_wire)

    # ── Assemble final wire Graph View (Baseline §3.2) ──────────────────────
    _timers.start('preselector')
    candidate_set = {
        "anchor": masked_anchor,
        "graph": {"edges": edges_kept_wire},
        "meta": {
            # mirror header if present; fallback to store etag
            "snapshot_etag": safe_etag,
            # canonicalize to ^sha256:[0-9a-f]{64}$ (guards double-hex)
            "policy_fp": normalize_fingerprint(str(policy.get("policy_fp") or "")),
            "fingerprints": { "graph_fp": fp_graph(wire_anchor_id, edges_kept_wire) },
            "alias": alias_block,
        },
    }
    # Observability: graph_fp computed (adoption/drift)
    try:
        _gfp = (candidate_set.get('meta') or {}).get('fingerprints', {}).get('graph_fp')
        if isinstance(_gfp, str) and _gfp:
            log_stage(logger, 'view', 'view.graph_fp_computed', graph_fp=_gfp)
            metric_counter('memory_view_graph_fp_computed_total', 1)
    except (TypeError, ValueError):
        pass

    # allowed_ids (WIRE) — non-optional (schema-required)
    candidate_set["meta"]["allowed_ids"] = _ids
    candidate_set["meta"]["allowed_ids_fp"] = fp_allowed_ids(_ids)
    candidate_set["meta"]["returned_count"] = len(_ids)
    try:
        log_stage(logger, "build_meta", "allowed_ids_set", count=len(_ids), sample=_ids[:3])
    except (RuntimeError, ValueError, TypeError, KeyError):
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
    except (RuntimeError, ValueError, TypeError, KeyError):
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
        _node_ids_list = compute_allowed_ids((candidate_set.get('anchor') or {}), _raw_edges)
        log_stage(
            logger, 'audit', 'hidden_counts',
            request_id=(payload.get('request_id') if isinstance(payload, dict) else None),
            snapshot_etag=safe_etag,
            policy_fp=(policy.get('policy_fp') if isinstance(policy, dict) else None),
            allowed_ids_fp=((candidate_set.get('meta') or {}).get('allowed_ids_fp') if isinstance(candidate_set, dict) else None),
            nodes_considered=len(_node_ids_list),
            edges_considered=int(_edge_count2),
        )
    except (RuntimeError, ValueError, TypeError, KeyError):
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
    except (RuntimeError, ValueError, TypeError) as e:
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
        except (RuntimeError, ValueError, TypeError, KeyError):
            pass
        raise HTTPException(status_code=500, detail="validation_error:graph_view_invalid")

    # Write-through store (same TTL as resolve cache)
    # Skip cache writes if the snapshot ETag is unknown to prevent cross-snapshot reuse.
    try:
        rc = RedisCache(get_redis_pool())
        cache_key = cache_keys.mem_expand_candidates(
            safe_etag, str(policy.get("policy_fp") or ""), anchor
        )
        if safe_etag == "unknown":
            log_stage(logger, "cache", "store_skipped_etag_unknown", layer="expand", cache_key=cache_key)
        else:
            await rc.setex(cache_key, int(TTL_EVIDENCE_CACHE_SEC), jsonx.dumps(candidate_set))
            log_stage(logger, "cache", "store", layer="expand", cache_key=cache_key, ttl=int(TTL_EVIDENCE_CACHE_SEC))
    except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
        pass
    res = _json_response_with_etag(candidate_set, safe_etag)
    # Surface as a header for the FE/audit drawer without touching the schema
    try:
        res.headers["X-BV-Alias-Followed"] = str(int(alias_followed))
    except (RuntimeError, ValueError, TypeError, KeyError):
        pass
    # Observability-only: include engine FP and structured pair log
    if engine_fp:
        res.headers[BV_POLICY_ENGINE_FP] = str(engine_fp)
    _log_policy_fp_pair(
        stage="expand_candidates",
        request_id=rid,
        local_fp=(policy.get("policy_fp") if isinstance(policy, dict) else None),
        engine_fp=engine_fp,
    )
    _maybe_add_policy_advice_header(res, request, str(policy.get("policy_fp") or ""))
    return res

