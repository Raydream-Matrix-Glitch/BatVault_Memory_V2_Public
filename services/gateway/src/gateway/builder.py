from __future__ import annotations
from typing import Any, Tuple, Dict
import time, uuid, os
from datetime import datetime, timezone
from core_models_gen import GraphEdgesModel
from core_utils.graph import derive_events_from_edges
from core_utils.fingerprints import canonical_json, sha256_hex
from core_logging import get_logger, trace_span, log_stage, current_request_id
from .signing import select_and_sign, SigningError
from core_models.ontology import CAUSAL_EDGE_TYPES, canonical_edge_type
try:
    # Prefer the selector's authoritative policy identifier
    from gateway.selector import SELECTOR_POLICY_ID as _SELECTOR_POLICY_ID  # type: ignore
except ImportError:
    # Safe fallback aligns with target.md §11 policy.selector_policy_id
    _SELECTOR_POLICY_ID = "sim_desc__ts_iso_desc__id_asc"

from core_utils import jsonx
from core_config import get_settings
import importlib.metadata as _md
from core_models_gen.models import (
    WhyDecisionAnchor,
    WhyDecisionAnswer,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    CompletenessFlags,
)
from gateway.orientation import classify_edge_orientation, assert_ready_for_orientation
from core_models_gen.models_meta_inputs import MetaInputs
from gateway import selector as _selector
from .templater import build_answer_blocks, apply_template
from .template_registry import select_template
from core_utils.domain import make_anchor
from core_models.meta_builder import build_meta
from core_metrics import counter as metric_counter, histogram as metric_histogram
from core_cache import keys as cache_keys
from core_cache.redis_cache import RedisCache
from core_cache.redis_client import get_redis_pool
from core_config.constants import TTL_BUNDLE_CACHE_SEC, TTL_EVIDENCE_CACHE_SEC
from core_idem import (
    idem_redis_key, idem_key_fp, idem_merge,
    idem_log_progress,
)


logger   = get_logger("gateway.builder")
_cache = None

def _get_cache() -> RedisCache:
    """Lazily construct a RedisCache bound to the shared aioredis pool.
    Avoids import-time failures and keeps gateway<>memory separation via core_cache.
    """
    global _cache
    if _cache is None:
        client = get_redis_pool()
        _cache = RedisCache(client)
    return _cache

# --- Minimal helpers for deterministic counts and answers -------------------

def _build_exec_summary_envelope(
    response_obj: Dict[str, Any],
    ts_utc: str,
    *,
    ev_obj: Any,
    mem_meta: Dict[str, Any],
    request_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    _t0 = time.perf_counter()
    """
    Schema-first projection:
      Public response.json = {anchor, graph:{edges}, meta, answer, completeness_flags}
      - Strip UI/diagnostic extras from anchor (e.g. mask_summary).
      - Sanitize meta to allowed keys only.
      - Keep only {graph_fp} under meta.fingerprints.
      - Compute bundle_fp deterministically as signature.covered and mirror it to meta.bundle_fp
        (never inside meta.fingerprints).
    """
    src = dict(response_obj or {})
    # Prefer explicit Evidence object; fallback to any embedded dict in response_obj.
    try:
        ev  = (ev_obj.model_dump(mode="python", exclude_none=True)
               if hasattr(ev_obj, "model_dump")
               else dict(ev_obj or {}))
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError):
        ev  = dict(src.pop("evidence", {}) or {})
    # Build minimal public shape
    # Anchor: prefer full object when complete; else canonical string id
    _raw_anchor = (src.get("anchor") or ev.get("anchor") or {})
    _anchor_id  = (
        (getattr(getattr(ev_obj, "anchor", None), "id", None))
        or (_raw_anchor.get("id") if isinstance(_raw_anchor, dict) else (_raw_anchor if isinstance(_raw_anchor, str) else ""))
        or ""
    )
    _required = ("id","type","title","domain","timestamp")
    _anchor_pub = (
        _raw_anchor
        if (isinstance(_raw_anchor, dict) and all(k in _raw_anchor and _raw_anchor[k] for k in _required))
        else str(_anchor_id)
    )
    public: Dict[str, Any] = {
        "anchor": _anchor_pub,
        "graph":  (src.get("graph")  or ev.get("graph")  or {"edges": []}),
        "answer": src.get("answer") or {"blocks": {"lead": ""}},
        "completeness_flags": src.get("completeness_flags") or {"has_preceding": False, "has_succeeding": False},
    }
    # Strip UI-only extras not present in the anchor schema
    if isinstance(public.get("anchor"), dict):
        public["anchor"].pop("mask_summary", None)
    # --- sanitize meta (schema-first; Memory is the single authority for these fields) ---
    gw_meta       = dict(src.get("meta") or {})
    mem_meta_safe = dict(mem_meta or {})
    fps_in        = dict((mem_meta_safe.get("fingerprints") or {}))
    _policy_block = dict(gw_meta.get("policy") or {})
    _fps_block    = dict(gw_meta.get("fingerprints") or {})

    # Build the Exec Summary meta strictly from Memory’s authoritative view.
    meta: Dict[str, Any] = {
        # Allowed IDs are carried for FE expand; sorted/stable upstream.
        "allowed_ids": list(
            (ev.get("allowed_ids") if isinstance(ev, dict) else []) or mem_meta_safe.get("allowed_ids") or []
        ),
        # REQUIRED by schema — no local fallbacks to avoid drift.
        "allowed_ids_fp": str((mem_meta_safe.get("allowed_ids_fp") or "")),
        "policy_fp":      str((mem_meta_safe.get("policy_fp") or "")),
        "snapshot_etag":  str((mem_meta_safe.get("snapshot_etag") or "")),
        # Deterministic knobs, derived locally but non-authoritative.
        "selector_policy_id": str(_policy_block.get("selector_policy_id") or _SELECTOR_POLICY_ID),
        "budget_cfg_fp":      str(_fps_block.get("budget_cfg_fp") or ""),
        # Only graph_fp is permitted inside fingerprints for response.json (bundle_fp lives next to it).
        "fingerprints": {"graph_fp": str(((mem_meta_safe.get("fingerprints") or {}).get("graph_fp") or fps_in.get("graph_fp") or ""))},
    }
    public["meta"] = meta
    log_stage(
        logger, "builder", "exec_summary_meta_fields",
        request_id=request_id,
        allowed_ids=len(meta.get("allowed_ids") or []),
        has_allowed_ids_fp=bool(meta.get("allowed_ids_fp")),
        has_snapshot_etag=bool(meta.get("snapshot_etag")),
        graph_fp=str((meta.get("fingerprints") or {}).get("graph_fp") or ""),
    )
    # PR-5: real signing (centralised). Sign the canonical public response and
    # mirror signature.covered to meta.bundle_fp.
    # IMPORTANT: Only graph_fp is allowed under meta.fingerprints; NEVER include bundle_fp there.
    signature = select_and_sign(public, request_id=request_id)
    public.setdefault("meta", {})["bundle_fp"] = signature["covered"]
    # Defensive: ensure fingerprints has only graph_fp (no bundle_fp inside)
    fps = public["meta"].get("fingerprints", {}) or {}
    fps.pop("bundle_fp", None)
    public["meta"]["fingerprints"] = fps
    # Invariant: mirror must match signature.covered
    assert public["meta"]["bundle_fp"] == signature["covered"], "bundle_fp must equal signature.covered"
    log_stage(logger, "builder", "bundle_signed", request_id=request_id,
              algo=signature.get("alg"),
              key_id=signature.get("key_id"))
    # (bundle_fp mirrored to meta by _build_exec_summary_envelope)  :contentReference[oaicite:5]{index=5}
    try:
        metric_histogram("gateway_stage_bundle_seconds", time.perf_counter() - _t0, step="envelope_and_sign")
    except (RuntimeError, ValueError, TypeError, OSError) as _merr:
        log_stage(logger, "builder", "metrics_emit_failed", error=str(_merr))
    return {"schema_version": "v3", "response": public}, signature, signature["covered"]

def _counts(ids_list, ev) -> dict:
    """Return simple counts for audit: number of ids and in-pool edges."""
    try:
        g_obj = getattr(ev, "graph", None)
        if hasattr(g_obj, "edges"):
            edges = [e for e in (getattr(g_obj, "edges") or []) if isinstance(e, dict)]
        elif isinstance(g_obj, dict):
            edges = [e for e in (g_obj.get("edges") or []) if isinstance(e, dict)]
        else:
            edges = []
    except (AttributeError, KeyError, TypeError, ValueError):
        edges = []
    idset = set(ids_list or [])
    edge_count = 0
    for e in edges:
        try:
            f = e.get("from") or e.get("from_id")
            t = e.get("to") or e.get("to_id")
            if f in idset and t in idset:
                edge_count += 1
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            log_stage(logger, "builder", "edge_count_skip",
                      error=type(exc).__name__, request_id=(current_request_id() or "unknown"))
            continue
    return {"ids": len(idset), "edges": edge_count}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_cited_ids(ev: WhyDecisionEvidence) -> list[str]:
    """Compute deterministic cited_ids for the given evidence.
    Ordering: [anchor] + [top 3 events (selector)] + [first succeeding decision].
    Only IDs in ev.allowed_ids are considered; duplicates removed. Env
    `cite_all_ids` (bool) forces using all allowed IDs in pool order.
    """
    try:
        s = get_settings()
        cite_all_env = "1" if getattr(s, "cite_all_ids", False) else ""
        cite_all = cite_all_env.lower() in ("true", "1", "yes", "y")
    except (RuntimeError, AttributeError, ValueError):
        cite_all = False
    allowed = list(getattr(ev, "allowed_ids", []) or [])
    if cite_all:
        return allowed
    support: list[str] = []
    # Anchor
    anchor_id = None
    anchor_type = None
    try:
        anchor_id = getattr(ev.anchor, "id", None)
        try:
            anchor_type = str(getattr(ev.anchor, "type", "")).upper()
        except (AttributeError, ValueError, TypeError):
            anchor_type = None
    except (AttributeError, KeyError, TypeError, ValueError):
        anchor_id = None
    if anchor_id and anchor_id in allowed:
        support.append(anchor_id)
    # Events: consider only LED_TO edges INTO the anchor (no alias/tail candidates).
    # Build candidate IDs from graph.edges, then filter derived events by this set.
    try:
        g_obj = getattr(ev, "graph", None)
        if hasattr(g_obj, "edges"):
            _edges = [e for e in (getattr(g_obj, "edges") or []) if isinstance(e, dict)]
        elif isinstance(g_obj, dict):
            _edges = [e for e in (g_obj.get("edges") or []) if isinstance(e, dict)]
        else:
            _edges = []
    except (AttributeError, TypeError, ValueError):
        _edges = []
    # Edge type canonicalizer used by both branches
    def _et(edge):
        try:
            return canonical_edge_type((edge or {}).get("type"))
        except ValueError:
            return None

    if (anchor_type or "") == "EVENT":
        # For EVENT anchors: cite decisions this event LED_TO (outbound)
        out_of_anchor = [
            (e.get("to") or e.get("to_id"), (e.get("timestamp") or ""))
            for e in _edges
            if _et(e) == "LED_TO" and e.get("from") == anchor_id
        ]
        # Order: newest first by timestamp, then id asc
        out_of_anchor.sort(key=lambda t: (t[1], str(t[0] or "")), reverse=True)
        for to_id, _ts in out_of_anchor[:3]:
            if to_id and to_id in allowed and to_id not in support:
                support.append(to_id)
    else:
        into_anchor = {
            (e.get("from") or e.get("from_id"))
            for e in _edges
            if _et(e) == "LED_TO" and e.get("to") == anchor_id
        }
        events: list[dict] = [evd for evd in derive_events_from_edges(ev) if evd.get("id") in into_anchor]
        try:
            log_stage(
                logger, "builder", "events_derived_from_edges",
                count=len(events), request_id=(current_request_id() or "unknown")
            )
        except (RuntimeError, ValueError, KeyError, TypeError):
            pass
        # Rank via shared helper; fallback to timestamp desc if anything goes wrong
        try:
            ranked = _selector.rank_events(ev.anchor, events)[:3]
        except (RuntimeError, ValueError, KeyError, TypeError):
            ranked = sorted(events, key=lambda x: (x.get("timestamp") or "", x.get("id") or ""), reverse=True)[:3]
        for evd in ranked:
            eid = evd.get("id")
            if eid and eid in allowed and eid in into_anchor and eid not in support:
                support.append(eid)
    # Include the first succeeding *decision* touching the anchor (the “Next” pointer).
    try:
        anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
        g_obj = getattr(ev, "graph", None)
        if hasattr(g_obj, "edges"):
            edges = [e for e in (getattr(g_obj, "edges") or []) if isinstance(e, dict)]
        elif isinstance(g_obj, dict):
            edges = [e for e in (g_obj.get("edges") or []) if isinstance(e, dict)]
        else:
            edges = []
        next_id = None
        # Prefer oriented succeeding CAUSAL edges (post-orientation).
        for e in edges:
            try:
                et = canonical_edge_type((e or {}).get("type"))
            except ValueError:
                continue
            if et == "CAUSAL" and (e.get("orientation") == "succeeding") and \
               (e.get("from") == anchor_id or e.get("to") == anchor_id):
                next_id = e.get("to"); break
        # Fallback: un-oriented direct CAUSAL out of anchor (strictly same-domain).
        if not next_id:
            for e in edges:
                try:
                    et = canonical_edge_type((e or {}).get("type"))
                except ValueError:
                    continue
                if et == "CAUSAL" and e.get("from") == anchor_id:
                    next_id = e.get("to"); break
        if next_id and next_id in allowed and next_id not in support:
            support.append(next_id)
    except (AttributeError, TypeError, ValueError):
        # Optional pointer — safe to proceed without it
        pass
    return support
try:
    _GATEWAY_VERSION = _md.version("gateway")
except _md.PackageNotFoundError:
    _GATEWAY_VERSION = "unknown"

# ---------------------------------------------------------------------------
# Orientation application (post-dedupe/filter; Baseline §5)
# ---------------------------------------------------------------------------
def _apply_orientation(ev_obj, *, anchor_id: str) -> tuple[int, int]:
    """
    Compute orientation ONLY for {LED_TO, CAUSAL}; never for ALIAS_OF.
    Must be invoked after evidence has been validated and deduped/clamped.
    Returns (preceding_count, succeeding_count).
    """
    if not anchor_id:
        raise ValueError("orientation_apply_failed: missing anchor_id")
    # Access edges-only graph from evidence; fail closed if shape invalid.
    # Graph is GraphEdgesModel; precondition asserts on a Mapping.
    g_raw = getattr(ev_obj, "graph", None)
    if isinstance(g_raw, GraphEdgesModel):
        g = g_raw.model_dump(mode="python", exclude_none=True)
    elif isinstance(g_raw, dict):
        g = g_raw
    else:
        g = (getattr(ev_obj, "__dict__", {}) or {}).get("graph") or {}
    if not isinstance(g, dict):
        g = {"edges": []}
    assert_ready_for_orientation(g)
    edges = list(g.get("edges") or [])
    if not edges:
        ev_obj.__dict__["graph"] = {"edges": []}
        return (0, 0)
    # Identify alias sources (ALIAS_OF: alias_event → anchor)
    alias_sources = {
        e.get("from") for e in edges
        if isinstance(e, dict)
        and (lambda _t: (_t == "ALIAS_OF"))(canonical_edge_type(e.get("type")))
        and (e.get("to") == anchor_id)
    }
    out = []
    pre_n = 0
    suc_n = 0
    for e in edges:
        if not isinstance(e, dict):
            continue
        try:
            et = canonical_edge_type(e.get("type"))
        except ValueError:
            out.append(e); continue  # unknown → neutral
        if et == "ALIAS_OF":
            e.pop("orientation", None)  # neutral
        elif et in CAUSAL_EDGE_TYPES:
            hint = "succeeding" if (e.get("from") in alias_sources and e.get("to") != anchor_id) else None
            orient = classify_edge_orientation(anchor_id, e, hint)
            if orient:
                e["orientation"] = orient
                pre_n += (1 if orient == "preceding" else 0)
                suc_n += (1 if orient == "succeeding" else 0)
            else:
                e.pop("orientation", None)
        out.append(e)
    ev_obj.graph = GraphEdgesModel(edges=out)
    return (pre_n, suc_n)

# ------ Evidence cache helpers ---------------------------------------------
def _evidence_cache_key_from_ev(ev: WhyDecisionEvidence) -> str | None:
    """
    Build the composite cache key evidence:{etag}|{allowed_ids_fp}|{policy_fp}
    Returns None if any part is missing.
    """
    meta = getattr(ev, "meta", None)
    if isinstance(meta, dict):
        allowed_ids_fp = str(meta.get("allowed_ids_fp") or "")
        policy_fp = str(meta.get("policy_fp") or (meta.get("fingerprints") or {}).get("policy_fingerprint") or "")
    else:
        allowed_ids_fp = str(getattr(meta, "allowed_ids_fp", "") or "")
        policy_fp = str(getattr(meta, "policy_fp", "") or getattr(getattr(meta, "fingerprints", None), "policy_fingerprint", "") or "")
    etag = str(getattr(ev, "snapshot_etag", "") or "")
    if etag and allowed_ids_fp and policy_fp:
        return cache_keys.evidence(etag, allowed_ids_fp, policy_fp)
    return None

async def store_evidence_cache(ev: WhyDecisionEvidence) -> None:
    """
    Persist the masked evidence JSON under its composite cache key with TTL_EVIDENCE_CACHE_SEC.
    No broad exception handling: simply no-ops if meta is incomplete.
    """
    k = _evidence_cache_key_from_ev(ev)
    if not k:
        return
    await _get_cache().setex(
        k,
        int(TTL_EVIDENCE_CACHE_SEC),
        jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)),
    )
    try:
        _rid = (current_request_id() or "unknown")
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError):
        _rid = "unknown"
    log_stage(logger, "cache", "store",
              layer="evidence", cache_key=k, ttl=int(TTL_EVIDENCE_CACHE_SEC),
              request_id=_rid)

# ───────────────────── main entry-point ─────────────────────────
async def build_why_decision_response(
    req: "AskIn",                          # forward-declared (defined in app.py)
    evidence_builder,                      # EvidenceBuilder instance (singleton passed from app.py)
    *,
    source: str = "query",                 # "ask" | "query" – used for policy/logging
    fresh: bool = False,                   # bypass caches when gathering evidence
    policy_headers: dict | None = None,    # pass policy headers through to Memory API
    stage_times: dict | None = None,       # STAGE 5: accumulate per-stage latencies (ms)
    gateway_plan: dict | None = None) -> Tuple[WhyDecisionResponse, Dict[str, bytes], str]:
    """
    Assemble Why-Decision response and audit artifacts.
    Returns (response, artifacts_dict, request_id).
    """
    t0      = time.perf_counter()
    # Prefer explicit request_id from the AskIn. Do NOT reuse a stale/global id.
    explicit = getattr(req, "request_id", None)
    if explicit:
        req_id = explicit.strip()
    else:
        # match the 16-hex convention used by MinIO paths
        req_id = uuid.uuid4().hex[:16]
    stage_times = dict(stage_times or {})
    artifacts: Dict[str, bytes] = {}
    settings = get_settings()

    # ── evidence (k = 1 collect) ───────────────────────────────
    ev: WhyDecisionEvidence
    if req.evidence is not None:
        ev = req.evidence
    elif req.anchor_id:
        # Pass policy headers through to EvidenceBuilder → Memory API
        ev = await evidence_builder.build(req.anchor_id, fresh=fresh, policy_headers=policy_headers)
        if ev is None:

            ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id=req.anchor_id))
            ev.snapshot_etag = "unknown"
    else:                       # safeguard – should be caught by AskIn validator
        ev = WhyDecisionEvidence(anchor=WhyDecisionAnchor(id="unknown"))

    # Common context for structured logs in exception paths.
    _ctx_anchor_id = (getattr(getattr(ev, "anchor", None), "id", None) or "unknown")
    _ctx_bundle_fp = "unknown"  # Filled once envelope fingerprints are computed.

    artifacts["evidence_pre.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()

    # Persist deterministic gateway plan (strongly typed by schema)
    if gateway_plan is None:
        raise ValueError("gateway_plan not provided")
    artifacts["gateway.plan.json"] = jsonx.dumps(gateway_plan).encode()

    # Persist canonicalised, pre-gate evidence (post-dedupe, pre-trim)
    artifacts["evidence_canonical.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()
    log_stage(logger, "builder", "evidence_final_persisted",
              request_id=req_id,
              allowed_ids=len(getattr(ev, "allowed_ids", []) or []))
    # Write-through to Redis evidence cache (keyed by snapshot/policy/allowed_ids)
    await store_evidence_cache(ev)

    try:
        from core_config import get_settings as _get_settings
        s = _get_settings()
        registry_url = getattr(s, "policy_registry_url", None)
        if registry_url:
            from core_models.policy_registry_cache import (
                fetch_policy_registry as _fetch_policy_registry,
                get_cached as _get_cached,
            )
            # See if we already have a cached policy registry; if not, fetch it.
            _cached, _ = _get_cached("/api/policy/registry")
            if _cached is None:
                await _fetch_policy_registry()
                log_stage(logger, "schema", "policy_registry_warmed", request_id=req_id, url=registry_url)
            else:
                log_stage(logger, "schema", "policy_registry_cache_hit", request_id=req_id)
        else:
            log_stage(logger, "schema", "policy_registry_warm_skipped", request_id=req_id)
    except (ImportError, AttributeError, RuntimeError, ValueError):
        # Do not reference registry_url here – it may not be set on exceptions.
        _m = getattr(ev, "meta", None)
        # Avoid dict-like access on Pydantic models (BaseModel has no .get)
        policy_fp_val = (_m.get("policy_fp") if isinstance(_m, dict) else getattr(_m, "policy_fp", None))
        allowed_ids_fp_val = (_m.get("allowed_ids_fp") if isinstance(_m, dict) else getattr(_m, "allowed_ids_fp", None))
        # fingerprints.graph_fp may be a dict or a model on typed Meta
        if isinstance(_m, dict):
            _fps = _m.get("fingerprints") or {}
            graph_fp_val = _fps.get("graph_fp")
        else:
            _fps = getattr(_m, "fingerprints", None)
            graph_fp_val = (_fps.get("graph_fp") if isinstance(_fps, dict) else getattr(_fps, "graph_fp", None))
        log_stage(
            logger, "schema", "policy_registry_warm_failed",
            request_id=req_id,
            snapshot_etag=getattr(ev, "snapshot_etag", None),
            policy_fp=policy_fp_val,
            allowed_ids_fp=allowed_ids_fp_val,
            graph_fp=graph_fp_val,
            anchor_id=_ctx_anchor_id, bundle_fp=_ctx_bundle_fp
        )
    # ---- Normalise commonly used variables for deterministic meta ----
    selector_policy = _SELECTOR_POLICY_ID
    retry_count = int(getattr(ev, "_retry_count", 0))
    prompt_fp = None  # no prompt when LLM is removed
    snapshot_etag_fp = getattr(ev, "snapshot_etag", "unknown")
    bundle_fp = None
    _pool_ids = list(getattr(ev, "allowed_ids", []) or [])
    _prompt_ids = list(_pool_ids)
    _payload_ids = list(_prompt_ids)
    gw_version = _GATEWAY_VERSION
    # Trace IDs (best-effort)
    # Best-effort OTEL context; avoid broad exceptions and add observable fallback.
    try:
        from opentelemetry import trace as _trace  # type: ignore
    except ImportError:
        _trace_id, _span_id = None, None
    else:
        try:
            _sp = _trace.get_current_span()
            _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
            _trace_id = f"{_ctx.trace_id:032x}" if _ctx and getattr(_ctx, "trace_id", 0) else None
            _span_id  = f"{_ctx.span_id:016x}" if _ctx and getattr(_ctx, "span_id", 0) else None
        except (AttributeError, ValueError, TypeError) as exc:
            _trace_id, _span_id = None, None
            log_stage(logger, "builder", "otel_context_missing",
                      request_id=(current_request_id() or "unknown"),
                      error=str(exc))
    _anchor_id = getattr(getattr(ev, "anchor", None), "id", None) or "unknown"
    # Selector audit
    try:
        ranked = _selector.rank_events(ev.anchor, derive_events_from_edges(ev))
        ranked_event_ids = [d.get("id") for d in ranked if isinstance(d, dict)]
        sel_scores = []
    except (AttributeError, TypeError, ValueError, RuntimeError) as _sel_err:
        ranked_event_ids, sel_scores = [], []
        log_stage(logger, "builder", "selector_rank_failed",
                  request_id=(current_request_id() or "unknown"), error=str(_sel_err))
    # Budget audit
    cleaned_metrics = {"prompt_truncation": False, "prompt_excluded_ids": []}
    # Use a prompt-only graph if the orchestrator attached one; otherwise fall back to the full graph.
    # Do NOT mutate ev.graph here.
    ev_prompt = getattr(ev, "_prompt_graph", None) or getattr(ev, "graph", None) or ev

    # STAGE 2.5: Clamp payload evidence to Memory pool (nodes-only)
    try:
        _pool = set(ev.allowed_ids or [])
        if _pool:
            # Clamp edges-only graph to pool (Baseline §5: Gateway never widens beyond allowed_ids).
            # Graph is a Pydantic model (GraphEdgesModel), not a dict.
            g_obj = getattr(ev, "graph", None)
            _edges = []
            if g_obj is not None and hasattr(g_obj, "edges"):
                _edges = [e for e in (g_obj.edges or []) if isinstance(e, dict)]
            else:
                # Defensive fallback for unexpected shapes; still read-only.
                g = (getattr(ev, "__dict__", {}) or {}).get("graph") or {}
                if isinstance(g, dict):
                    _edges = [e for e in (g.get("edges") or []) if isinstance(e, dict)]
            def _ok_edge(e: dict) -> bool:
                f = e.get("from"); to = e.get("to")
                return (f in _pool) and (to in _pool)
            _edges_after = [e for e in _edges if _ok_edge(e)]
            ev.graph = GraphEdgesModel(edges=_edges_after)
            try:
                log_stage(logger, "builder", "graph_clamped_to_pool",
                          edges_before=len(_edges), edges_after=len(_edges_after))
            except (RuntimeError, ValueError, TypeError, KeyError) as _lg_err:
                log_stage(logger, "builder", "graph_clamp_log_failed",
                          request_id=(current_request_id() or "unknown"), error=str(_lg_err))
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError) as e:
        # Non-fatal: log with context and continue (Baseline: no silent errors).
        log_stage(logger, "builder", "pool_clamp_failed",
                  request_id=req_id, error=str(e),
                  anchor_id=_ctx_anchor_id, bundle_fp=_ctx_bundle_fp)

    # ── Orientation Writer (placement lock): run AFTER dedupe/filter+gate (Baseline §5)
    try:
        _aid_for_orient = getattr(getattr(ev, "anchor", None), "id", None)
        pre_n, suc_n = _apply_orientation(ev, anchor_id=str(_aid_for_orient or ""))
        log_stage(logger, "orientation", "applied",
                  request_id=req_id, preceding=pre_n, succeeding=suc_n)
    except ValueError as e:
        # Fail closed on precondition/shape violations; add context and re-raise.
        log_stage(logger, "orientation", "apply_failed",
                  request_id=req_id, error=str(e),
                  anchor_id=_ctx_anchor_id, bundle_fp=_ctx_bundle_fp)
        raise
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError) as e:
        log_stage(logger, "orientation", "apply_exception",
                  request_id=req_id, error=type(e).__name__,
                  anchor_id=_ctx_anchor_id, bundle_fp=_ctx_bundle_fp)
        raise
    artifacts["evidence_post.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()
    # v3: events not attached to evidence; ranking kept internal for meta if needed.

# Build new meta inputs
    _ts_utc = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    # --- policy meta (target.md §11) -----------------------------------------
    # Policy identifiers (override-able via env for easy rollouts)
    _policy_id   = os.getenv("WHY_POLICY_ID", "why_v1")
    _prompt_id   = os.getenv("WHY_PROMPT_ID", "why_v1.0")
    # Allowed-IDs policy: Gateway does not cap by policy; Pre-Selector already enforced ACL.
    # We report 'include_all' (no cap) unless a future preselector returns a cap reason.
    allowed_ids_policy = {
        "mode": "include_all",
        "cap_k": None,
        "cap_basis": None,
        "cap_reason": None,
    }
    # Use the canonical CAUSAL edge type in the allowlist.  The legacy
    # CAUSAL_PRECEDES has been removed from the ontology.
    edge_allowlist = list(CAUSAL_EDGE_TYPES)
    policy_meta = {
        "policy_id": _policy_id,
        "selector_policy_id": selector_policy,
        "allowed_ids_policy": allowed_ids_policy,
        "edge_allowlist": edge_allowlist,
    }
    log_stage(logger, "builder", "policy_meta_populated",
              request_id=req_id, policy_id=_policy_id, selector_policy_id=selector_policy)


    # ── Stage 9: Canonical telemetry keys (per-stage + total) ─────────────
    # Hoisted out of MetaInputs(...) to avoid syntax errors and to keep the
    # function call strictly keyword-only.
    _canonical_map = {
        "intent_resolve": "anchor",
        "expand_raw": "graph",
        "selector_rank": "selector",
        "render_response": "render",
    }
    _stage_latencies_canonical = {}
    try:
        for _k, _v in (stage_times or {}).items():
            _name = _canonical_map.get(_k, _k)
            _stage_latencies_canonical[_name] = int(_v)
    except (TypeError, ValueError, AttributeError) as _lt_err:
        log_stage(logger, "builder", "latency_canonicalize_failed", error=str(_lt_err))
        _stage_latencies_canonical = dict(stage_times or {})
    # Ensure total is present alongside per-stage values
    try:
        _stage_latencies_canonical["total"] = int((time.perf_counter() - t0) * 1000)
    except (OverflowError, TypeError, ValueError) as _lt2_err:
        log_stage(logger, "builder", "latency_total_failed", error=str(_lt2_err))
    # Request-level timeout budget (best-effort)
    try:
        from core_config.constants import TIMEOUT_LLM_MS, TIMEOUT_ENRICH_MS, TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS, TIMEOUT_VALIDATE_MS
        _timeout_ms = max(TIMEOUT_LLM_MS, TIMEOUT_ENRICH_MS, TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS, TIMEOUT_VALIDATE_MS)
    except (ImportError, AttributeError, ValueError) as _tc_err:
        log_stage(logger, "builder", "timeout_constants_unavailable", error=str(_tc_err))
        _timeout_ms = 0
    runtime_dict = {
        "latency_ms_total": int((time.perf_counter() - t0) * 1000),
        "stage_latencies_ms": _stage_latencies_canonical,
        "retries": int(retry_count),
        "cache_hit": False,
        "timeout_ms": _timeout_ms,
    }

    # Resolve allowed_ids_fp from Memory meta ONLY (no local recomputation; Baseline § single-source).
    _mm = getattr(ev, "meta", None)
    _allowed_ids_fp = (_mm.get("allowed_ids_fp") if isinstance(_mm, dict)
                       else getattr(_mm, "allowed_ids_fp", None))

    meta_inputs = MetaInputs(
        request={
            "intent": req.intent,
            "anchor_id": _anchor_id,
            "id": req_id,         # <-- schema requires 'id'
            "trace_id": _trace_id,
            "span_id": _span_id,
            "ts_utc": _ts_utc,    # <-- now seconds precision
        },
        policy={
            "policy_id": _policy_id, "prompt_id": _prompt_id,
            "allowed_ids_policy": {"mode": "include_all"},
            # Edge allowlist required by target.md policy block
            "edge_allowlist": list(CAUSAL_EDGE_TYPES),
            "gateway_version": gw_version, "selector_policy_id": selector_policy,
            "env": {"cite_all_ids": bool(getattr(settings, "cite_all_ids", False)),
                    "load_shed": bool(getattr(settings, "load_shed_enabled", False))}
        },
        budgets={
            # Static zeros – no LLM budgeting in play
            "context_window": 0,
            "desired_completion_tokens": 0,
            "guard_tokens": 0,
            "overhead_tokens": 0,
        },
        fingerprints={
            "prompt_fp": str(prompt_fp or "unknown"),
            # Schema requires a string; we replace this after response serialization
            "bundle_fp": str(bundle_fp or "pending"),
            "snapshot_etag": str(snapshot_etag_fp or "unknown"),
            "budget_cfg_fp": getattr(ev, "_budget_cfg_fp", None),
            "allowed_ids_fp": _allowed_ids_fp,
        },
        evidence_counts={
            # Pool: everything discovered (k=1 expansions etc.) and allowed
            "pool": _counts(_pool_ids, ev),
            # Prompt: only what actually made it into the prompt after token budgeting
            "prompt_included": _counts(_pool_ids, ev_prompt),  # computed on gated prompt view
            # Payload: what is serialized back to client
            "payload_serialized": _counts(_payload_ids, ev),
        },
        evidence_sets={
            "pool_ids": _pool_ids,
            # IDs included in the prompt (post-trim). If no trim happened, equals pool.
            "prompt_included_ids": _pool_ids,
            # Structured reasons from budget gate (e.g., {"id": "...", "reason": "token_budget"})
            "prompt_excluded_ids": (cleaned_metrics or {}).get("prompt_excluded_ids", []),
            "payload_included_ids": _payload_ids,
            "payload_excluded_ids": [],
            "payload_source": "pool",  # LLM mode is off; payloads are sourced from pool
        },
        # Schema-conformant selection/truncation metrics
        selection_metrics={
            "ranking_policy": selector_policy,
            "ranked_pool_ids": [i for i in ([_anchor_id] + ranked_event_ids) if i in (_pool_ids or [])],
            "ranked_prompt_ids": [i for i in ([_anchor_id] + ranked_event_ids) if i in ((_prompt_ids or _pool_ids) or [])],
            "scores": {},  # dict required; empty when selector scores aren’t available
        },
        truncation_metrics={
            # TruncationPass objects; one per shrink. max_prompt_tokens optional.
            "passes": [],
            "prompt_truncation": bool(
                False
            ),
        },
        # Stage-9 runtime telemetry (canonical keys)
        runtime=runtime_dict,
        load_shed=False,
    )

    # Build the canonical MetaInfo once; idempotent by design.
    _t_render = time.perf_counter()
    meta_obj = build_meta(meta_inputs, request_id=req_id)

    # ---- M3: enrich meta with policy_trace + downloads (dict-level, no model changes) ----
    try:
        policy_trace_val = getattr(ev, "_policy_trace", {}) or {}
    except (AttributeError, TypeError, ValueError):
        policy_trace_val = {}
    # Compute download gating based on actor role; directors/execs may access full bundles.
    try:
        actor_role = (getattr(meta_obj, "actor", {}) or {}).get("role")
    except (AttributeError, TypeError, ValueError) as _ar_err:
        log_stage(logger, "builder", "actor_role_read_failed", error=str(_ar_err))
        actor_role = None
    allow_full = str(actor_role).lower() in ("ceo",)
    downloads_manifest = {
        "artifacts": [
            {
                "name": "bundle_view",
                "allowed": True,
                "reason": None,
                "href": f"/v3/bundles/{req_id}/download?name=bundle_view"
            },
            {
                "name": "bundle_full",
                "allowed": bool(allow_full),
                "reason": None if allow_full else "acl:sensitivity_exceeded",
                "href": f"/v3/bundles/{req_id}/download?name=bundle_full"
            },
        ]
    }
    # Work on a plain dict so we don't depend on MetaInfo extra fields
    meta_dict = meta_obj if isinstance(meta_obj, dict) else meta_obj.model_dump()
    # Propagate policy_fp (Memory authoritative).  Prefer meta.policy_fp and fall back to
    # meta.fingerprints.policy_fingerprint for a limited compatibility window.  Do not
    # recompute fingerprints from local policy state (Baseline §1.1).
    try:
        _mm = getattr(ev, "meta", None)
        _pfp = None
        if isinstance(_mm, dict):
            _pfp = (_mm.get("policy_fp") or ((_mm.get("fingerprints") or {}).get("policy_fingerprint")))
        else:
            # typed Meta objects: attempt attribute access
            try:
                _pfp = getattr(_mm, "policy_fp", None)
                if not _pfp:
                    fps = getattr(_mm, "fingerprints", {}) or {}
                    _pfp = fps.get("policy_fingerprint")
            except (AttributeError, TypeError, ValueError):
                _pfp = None
        # Propagate only if present; do not derive a new fingerprint locally.
        if _pfp:
            meta_dict["policy_fp"] = _pfp
            meta_dict.setdefault("fingerprints", {})["policy_fingerprint"] = _pfp
            log_stage(logger, "builder", "policy_fingerprint", action="propagate", fp=_pfp)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        pass
    meta_dict["policy_trace"] = policy_trace_val
    meta_dict["downloads"] = downloads_manifest
    # Populate payload_excluded_ids from policy_trace (acl/redaction guards)
    try:
        _reasons = dict(policy_trace_val.get("reasons_by_id") or {})
        _payload_excl = [{"id": k, "reason": v} for k, v in _reasons.items() if isinstance(v, str) and v.startswith("acl:")]
        meta_dict.setdefault("evidence_sets", {}).setdefault("payload_excluded_ids", [])
        meta_dict["evidence_sets"]["payload_excluded_ids"] = _payload_excl
        if _payload_excl:
            log_stage(logger, "builder", "payload_exclusions_recorded", request_id=req_id, count=len(_payload_excl))
    except Exception:
        pass
    try:
        # Extract the snapshot etag from the evidence or fallback to the meta value.
        ev_etag = (
            getattr(ev, "snapshot_etag", None)
            or getattr(meta_obj, "snapshot_etag", None)
            or "unknown"
        )
        anchor_id = getattr(ev.anchor, "id", None) or "unknown"
        log_stage(logger, "builder", "etag_propagated",
                  anchor_id=anchor_id, snapshot_etag=ev_etag, request_id=req_id)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        pass

    bundle_url = f"/v3/bundles/{req_id}"
    # Will be computed after final response serialization; initialize for early references.
    bundle_fp_final: str | None = None
    # Prefer dict-style assignment; only fall back to attribute when not a mapping.
    if isinstance(meta_obj, dict):
        meta_obj.setdefault("resolver_path", "direct")
    else:
        try:
            if not getattr(meta_obj, "resolver_path", None):
                setattr(meta_obj, "resolver_path", "direct")
        except Exception as _rp_err:
            log_stage(logger, "builder", "resolver_path_dict_failed", error=str(_rp_err))

    # Strategic: single audit log of pool/prompt/payload cardinalities
    log_stage(logger, "meta", "pool_prompt_payload",
              request_id=req_id,
              pool=len(_pool_ids or []),
              prompt=len((_prompt_ids or [])),
              payload=len((_payload_ids or [])))

    # Return response with enriched meta (dict)
    try:
        stage_times['render_response'] = int((time.perf_counter() - _t_render) * 1000)
    except (OverflowError, TypeError, ValueError):
        pass
    # Compute completeness flags from oriented graph edges (no transitions in public bundle)
    try:
        # Accept Evidence (.graph.edges), a plain dict, or a GraphEdgesModel directly (.edges)
        if hasattr(ev, "edges") and isinstance(getattr(ev, "edges", None), list):
            edges = [e for e in (getattr(ev, "edges") or []) if isinstance(e, dict)]
        else:
            g_obj = getattr(ev, "graph", None)
            if hasattr(g_obj, "edges"):
                edges = [e for e in (getattr(g_obj, "edges") or []) if isinstance(e, dict)]
            elif isinstance(g_obj, dict):
                edges = [e for e in (g_obj.get("edges") or []) if isinstance(e, dict)]
            else:
                edges = []
    except (AttributeError, TypeError, ValueError):
        edges = []
    try:
        has_pre = any(str((e or {}).get("orientation") or "").lower() == "preceding" for e in edges)
        has_suc = any(str((e or {}).get("orientation") or "").lower() == "succeeding" for e in edges)
    except (TypeError, ValueError, AttributeError):
        has_pre, has_suc = False, False
    try:
        _event_count = len(derive_events_from_edges(ev))
    except (TypeError, ValueError, AttributeError):
        _event_count = 0
    flags = CompletenessFlags(
        has_preceding=bool(has_pre),
        has_succeeding=bool(has_suc),
        event_count=_event_count,
    )
    log_stage(logger, "builder", "completeness_flags_type",
              given=f"{flags.__class__.__module__}.{flags.__class__.__name__}",
              request_id=req_id)
    # 1) Compute full deterministic blocks from evidence
    blocks = build_answer_blocks(ev)
    # 2) Resolve template (org-aware) and apply it; fail-closed on invalid inputs
    try:
        template_id = str(getattr(req, "template_id", "") or "").strip() or None
        org_id = str(getattr(req, "org", "") or "").strip() or None
        tmpl, reg_fp = select_template(template_id, org_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        from fastapi import HTTPException
        _ctx = ""
        if isinstance(exc, (KeyError, FileNotFoundError)):
            _ctx = f":template={template_id or 'default'}@org={org_id or 'default'}"
        raise HTTPException(status_code=400, detail=f"template_error:{type(exc).__name__}{_ctx}")
    t_blocks = apply_template(blocks, tmpl)
    # 3) Compute deterministic citations within allowed_ids (unchanged policy)
    cited = _compute_cited_ids(ev)
    ans = WhyDecisionAnswer(blocks=t_blocks, cited_ids=cited)

    # Forward-looking shape: WhyDecisionResponse requires top-level anchor/graph.
    # Keep extra fields (e.g., intent, bundle_url) harmlessly via extra="allow".
    # Surface the chosen template and registry fingerprint in meta (public, signed at envelope)
    try:
        meta_dict["answer_template_id"] = str(tmpl.get("id"))
        meta_dict["template_registry_fp"] = str(reg_fp)
    except (AttributeError, KeyError, TypeError, ValueError) as _tmpl_err:
        # Do not fabricate fields if meta_dict shape changes; upstream validator will catch missing keys if required.
        log_stage(logger, "builder", "template_meta_propagate_failed", error=str(_tmpl_err))

    resp = WhyDecisionResponse(
        anchor=getattr(ev, "anchor", None) or WhyDecisionAnchor(),
        graph=getattr(ev, "graph", None) or GraphEdgesModel(edges=[]),
        answer=ans,
        completeness_flags=flags,
        meta=meta_dict,
        intent=getattr(req, "intent", None),
        bundle_url=bundle_url,
    )
    # Build v3 Exec Summary envelope (single source of truth) and compute bundle_fp deterministically.
    # Use Memory's authoritative meta for Exec Summary required fields (allowed_ids_fp, policy_fp, snapshot_etag).
    _mem_meta_obj = getattr(ev, "meta", None)
    if _mem_meta_obj is None:
        from fastapi import HTTPException
        log_stage(logger, "builder", "memory_meta_missing", anchor_id=_anchor_id)
        raise HTTPException(status_code=502, detail={"error": "memory_meta_missing"})
    # Convert Pydantic model → dict (JSON-first) deterministically; no local recomputation.
    _mem_meta = (
        _mem_meta_obj.model_dump(mode="python", by_alias=True, exclude_none=True)
        if hasattr(_mem_meta_obj, "model_dump") else dict(_mem_meta_obj)
    )
    _mm_fps   = dict((_mem_meta.get("fingerprints") or {}))
    # Preflight: required fields must be present from Memory (fail-closed).
    _missing_keys = [k for k in ("allowed_ids_fp", "policy_fp", "snapshot_etag") if not _mem_meta.get(k)]
    if _missing_keys:
        from fastapi import HTTPException
        log_stage(
            logger, "builder", "memory_meta_missing_required",
            missing=_missing_keys, anchor_id=_anchor_id
        )
        raise HTTPException(status_code=502, detail={"error": "memory_meta_missing", "missing": _missing_keys})
    log_stage(logger, "builder", "memory_meta_presence",
            has_allowed_ids_fp=bool(_mem_meta.get("allowed_ids_fp")),
            has_policy_fp=bool(_mem_meta.get("policy_fp")),
            has_snapshot_etag=bool(_mem_meta.get("snapshot_etag")),
            graph_fp=str(_mm_fps.get("graph_fp") or ""))
    envelope, signature, bundle_fp_final = _build_exec_summary_envelope(
        response_obj=resp.model_dump(mode="python", by_alias=True, exclude_none=True),
        ts_utc=str(meta_dict.get("request", {}).get("ts_utc") or _ts_utc),
        ev_obj=ev,
        mem_meta=(getattr(ev, "meta", None) or {}),
        request_id=req_id,
    )
    # OPTIONAL: record idempotency progress as soon as the bundle is known (best-effort)
    _idem_hdr = getattr(req, "idempotency_key", None) or getattr(req, "Idempotency_Key", None)  # if you plumb it in future
    if isinstance(_idem_hdr, str) and _idem_hdr:
        try:
            rc = get_redis_pool()
            if rc is not None and isinstance(bundle_fp_final, str) and bundle_fp_final:
                _key = idem_redis_key(_idem_hdr)
                await idem_merge(rc, _key, {"progress": {"bundle_fp": str(bundle_fp_final)}})
                idem_log_progress(logger, key_fp=idem_key_fp(_idem_hdr), bundle_fp=str(bundle_fp_final))
        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
            pass
    _ctx_bundle_fp = bundle_fp_final or _ctx_bundle_fp
    artifacts["response.json"] = jsonx.dumps(envelope).encode("utf-8")
    artifacts["receipt.json"]  = jsonx.dumps(signature).encode("utf-8")
    # Optional: persist the bundle to Redis using the canonical key (now that bundle_fp is known).
    try:
        redis_client = get_redis_pool()
        if redis_client is not None and isinstance(bundle_fp_final, str) and bundle_fp_final:
            rc = RedisCache(redis_client)
            # Artifacts are JSON → store a single JSON object for speed (deterministic: sort keys).
            serialized = jsonx.dumps(
                {k: (v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else str(v)) for k, v in artifacts.items()}
            ).encode("utf-8")
            await rc.setex(cache_keys.bundle(bundle_fp_final), int(TTL_BUNDLE_CACHE_SEC), serialized)
            log_stage(logger, "bundle", "redis_cached",
                      bundle_fp=bundle_fp_final, bytes=len(serialized))
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError) as _exc:
        log_stage(logger, "bundle", "redis_cache_skip",
                  reason=type(_exc).__name__, anchor_id=_ctx_anchor_id, bundle_fp=(bundle_fp_final or _ctx_bundle_fp))
    # Build compact trace (unsigned). Emission is unconditional (View bundle requires it).
    # Trace: read edges via the typed model (GraphEdgesModel) — avoid brittle __dict__ access
    try:
        g_obj = getattr(ev, "graph", None)
        if hasattr(g_obj, "edges"):
            _edges = [e for e in (getattr(g_obj, "edges") or []) if isinstance(e, dict)]
        elif isinstance(g_obj, dict):
            _edges = [e for e in (g_obj.get("edges") or []) if isinstance(e, dict)]
        else:
            _edges = []
    except (RuntimeError, ValueError, KeyError, TypeError) as e:
        log_stage(logger, "trace", "edges_read_failed", request_id=req_id, error=type(e).__name__)
        _edges = []
    orientation_counts = {
        "preceding": int(sum(1 for e in _edges if (e or {}).get("orientation") == "preceding")),
        "succeeding": int(sum(1 for e in _edges if (e or {}).get("orientation") == "succeeding")),
    }
    _ts = str(meta_dict.get("request", {}).get("ts_utc") or _ts_utc)
    trace_base = {
        "request": {
            "intent": getattr(req, "intent", None) or "why_decision",
            "anchor_id": getattr(getattr(ev, "anchor", None), "id", None),
            "request_id": req_id,
            "trace_id": getattr(meta_inputs.request, "trace_id", None),
            "ts_utc": _ts,
        },
        "memory_view": {
            "snapshot_etag": meta_dict.get("snapshot_etag") or (meta_dict.get("fingerprints") or {}).get("snapshot_etag"),
            "policy_fp": meta_dict.get("policy_fp") or (meta_dict.get("fingerprints") or {}).get("policy_fingerprint"),
            "allowed_ids_fp": (meta_dict.get("allowed_ids_fp")
                               or (meta_dict.get("fingerprints") or {}).get("allowed_ids_fp")),
            "fingerprints": {"graph_fp": (meta_dict.get("fingerprints") or {}).get("graph_fp")},
            "allowed_ids_count": int(len(getattr(ev, "allowed_ids", []) or [])),
            "edge_count": int(len(_edges)),
        },
        "gateway_path": {
            "selector_policy_id": _SELECTOR_POLICY_ID,
            "orientation_counts": orientation_counts,
            "events_ranked_top3": list(ranked_event_ids or [])[:3],
            "templater": {"mode": "deterministic", "template_version": "v3-blocks"},
            "bundle_fp": bundle_fp_final,
        },
        "response_preview": {
            "blocks": getattr(resp.answer, "blocks", {}),
            "completeness_flags": {
                "has_preceding": bool(getattr(resp.completeness_flags, "has_preceding", False)),
                "has_succeeding": bool(getattr(resp.completeness_flags, "has_succeeding", False)),
                "event_count": int(getattr(resp.completeness_flags, "event_count", 0)),
            },
        },
    }
    # Trace must respect bundles.trace.json (no extra top-level keys).
    trace_view = {
        "request": trace_base.get("request", {}),
        "gateway_path": trace_base.get("gateway_path", {}),
        "response_preview": trace_base.get("response_preview", {}),
        "validator": {},  # filled by run_validator() below
    }
    artifacts["trace.json"] = jsonx.dumps(trace_view).encode("utf-8")
    # Compute bundle.manifest.json deterministically over current artifacts BEFORE validation
    import mimetypes
    items = []
    for name in sorted(artifacts.keys()):
        blob = artifacts[name]
        h = sha256_hex(blob)
        ctype = mimetypes.guess_type(name, strict=False)[0] or "application/json"
        items.append({"name": name, "sha256": h, "bytes": len(blob), "content_type": ctype})
    artifacts["bundle.manifest.json"] = jsonx.dumps({"artifacts": items}).encode("utf-8")
    # Sanity checks
    assert "bundle.manifest.json" in artifacts, "builder: missing bundle.manifest.json before validation"

    # Stage-7 validator report (delegates to core validator) and fail-closed.
    from .validator import view_artifacts_allowed
    from .validator import run_validator as _run_validator
    _allowed = view_artifacts_allowed()
    _artifacts_for_view = {k: v for k, v in artifacts.items() if k in _allowed}
    # Recompute a bundle.manifest.json strictly for the view subset.
    # IMPORTANT: exclude the manifest itself when computing the manifest to avoid
    # self-hash/size mismatches (the subset currently contains the full-bundle manifest).
    _artifacts_for_view.pop("bundle.manifest.json", None)
    _items_view = []
    for _name in sorted(_artifacts_for_view.keys()):
        _blob = _artifacts_for_view[_name]
        _sha  = sha256_hex(_blob)
        _ctype = mimetypes.guess_type(_name, strict=False)[0] or "application/json"
        _items_view.append({"name": _name, "sha256": _sha, "bytes": len(_blob), "content_type": _ctype})
    _artifacts_for_view["bundle.manifest.json"] = jsonx.dumps({"artifacts": _items_view}).encode("utf-8")
    # Defensive: the view manifest must not list itself (prevents non-convergent fixed points).
    _mn = [it.get("name") for it in (jsonx.loads(_artifacts_for_view["bundle.manifest.json"]).get("artifacts") or [])]
    assert "bundle.manifest.json" not in _mn, "view manifest must not list itself"
    # Sanity: validator input must include a manifest that matches its own subset.
    assert "bundle.manifest.json" in _artifacts_for_view, "validator input missing bundle.manifest.json"
    # Helpful audit log of exactly what the validator sees.
    log_stage(
        logger, "validator", "input_inventory",
        request_id=req_id,
        full_count=len(artifacts or {}),
        view_count=len(_artifacts_for_view or {}),
        view_names=sorted(_artifacts_for_view.keys()),
    )
    _val_report = _run_validator(
        envelope,   # validate the response (bundle_fp still lives in meta)
        _artifacts_for_view,
        request_id=req_id,
    )
    artifacts["validator_report.json"] = jsonx.dumps(_val_report).encode("utf-8")

    # Enrich trace with validator outcome (best-effort; keep base trace on failure).
    try:
        _trace = jsonx.loads(artifacts["trace.json"])
        _trace.setdefault("gateway_path", {})["validation"] = {
            "error_count": int(len(_val_report.get("errors", []) or []))
        }
        artifacts["trace.json"] = jsonx.dumps(_trace).encode("utf-8")
    except (KeyError, ValueError, TypeError) as e:
        log_stage(logger, "trace", "validation_enrich_failed", request_id=req_id, error=type(e).__name__)
    # Baseline metric: count validator errors (kept, but emitted in a single place).
    try:
        metric_counter("validator_errors", float(len(_val_report.get("errors", []))), request_id=req_id)
    except (RuntimeError, ValueError, TypeError) as _vc_err:
        log_stage(logger, "metrics", "validator_errors_emit_failed", error=str(_vc_err), request_id=req_id)
    if not bool(_val_report.get("pass")):
        try:
            _mismatch = next((e for e in (_val_report.get("errors") or []) if e.get("code") == "manifest_mismatch"), None)
            _manifest_issues = ((_mismatch or {}).get("issues"))
            log_stage(logger, "validator", "bundle_failed",
                      request_id=req_id,
                      anchor_id=_ctx_anchor_id,
                      bundle_fp=(bundle_fp_final or "unknown"),
                      errors=[e.get("code") for e in (_val_report.get("errors") or [])][:10],
                      manifest_issues=_manifest_issues)
        except (KeyError, TypeError, ValueError) as _vf_log_err:
            log_stage(logger, "validator", "bundle_failed_log_skipped", error=str(_vf_log_err))
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail={"error": "bundle_validation_failed", "errors": (_val_report.get("errors") or [])[:5]},
        )

    # Optionally persist artifacts to MinIO without blocking the request path.
    try:
        # Lazy-import to avoid hard runtime dep in tests/local.
        from gateway.app import _minio_put_batch_async as _minio_save
        import asyncio as _asyncio
        # Snapshot the dict to avoid concurrent mutations while uploading
        _snapshot = dict(artifacts)
        _asyncio.create_task(_minio_save(req_id, _snapshot))
    except (ImportError, RuntimeError, OSError) as _minio_err:
        # MinIO not configured or background scheduling failed — log and continue.
        log_stage(logger, "builder", "artifact_upload_skipped",
                  request_id=(current_request_id() or "unknown"),
                  error=str(_minio_err))
    return resp, artifacts, req_id
