from __future__ import annotations
from typing import Any, Tuple, Dict
import time, uuid, os, hashlib
from datetime import datetime, timezone
from core_models_gen import GraphEdgesModel
from core_utils.graph import derive_events_from_edges
from core_utils.fingerprints import canonical_json, sha256_hex, ensure_sha256_prefix, allowed_ids_fp as compute_allowed_ids_fp
from .signing import select_and_sign, SigningError
from core_models.ontology import CAUSAL_EDGE_TYPES
try:
    # Prefer the selector's authoritative policy identifier
    from gateway.selector import SELECTOR_POLICY_ID as _SELECTOR_POLICY_ID  # type: ignore
except ImportError:
    # Safe fallback aligns with target.md §11 policy.selector_policy_id
    _SELECTOR_POLICY_ID = "sim_desc__ts_iso_desc__id_asc"

from core_utils import jsonx
from core_config import get_settings
from core_models.ontology import assert_truncation_action

import importlib.metadata as _md
from core_logging import get_logger, trace_span, log_stage, current_request_id
from core_models_gen.models import (
    WhyDecisionAnchor,
    WhyDecisionAnswer,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    CompletenessFlags,
)
from gateway.orientation import classify_edge_orientation, assert_ready_for_orientation
from core_models.meta_inputs import MetaInputs
from gateway import selector as _selector
from .templater import finalise_short_answer, deterministic_short_answer
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
) -> Tuple[Dict[str, Any], str]:
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
    except Exception:
        ev  = dict(src.pop("evidence", {}) or {})
    # Build minimal public shape
    public: Dict[str, Any] = {
        "anchor": (src.get("anchor") or ev.get("anchor") or {}),
        "graph":  (src.get("graph")  or ev.get("graph")  or {"edges": []}),
        "answer": src.get("answer") or {"short_answer": "", "cited_ids": []},
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
    # mirror signature.covered to meta.bundle_fp. Only graph_fp is allowed under meta.fingerprints.
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
    try:
        metric_histogram("gateway_stage_bundle_seconds", time.perf_counter() - _t0, step="envelope_and_sign")
    except (RuntimeError, ValueError, TypeError):
        pass
    return {"schema_version": "v3", "response": public, "signature": signature}, signature["covered"]

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

def _build_minimal_answer(ev: WhyDecisionEvidence) -> WhyDecisionAnswer:
    """Deterministic, non-LLM short answer with cited ids."""
    try:
        cited = _compute_cited_ids(ev)
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError):
        cited = list(getattr(ev, "allowed_ids", []) or [])
    # Build a stub first, then deterministically finalise; on failure, hard-fallback.
    ans = WhyDecisionAnswer(short_answer="STUB ANSWER", cited_ids=cited)
    try:
        ans, _ = finalise_short_answer(ans, ev)
        return ans
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
        # Strategic log + deterministic fallback => never surface a stub
        log_stage(logger, "templater", "finalise_failed",
                  error=type(e).__name__, message=str(e),
                  request_id=(current_request_id() or "unknown"))
        try:
            txt, planned = deterministic_short_answer(ev)
            ans.short_answer = txt
            ans.cited_ids = planned
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as ee:
            log_stage(logger, "templater", "fallback_failed",
                      error=type(ee).__name__, request_id=(current_request_id() or "unknown"))
        return ans

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
    if (anchor_type or "") == "EVENT":
        # For EVENT anchors: cite decisions this event LED_TO (outbound)
        out_of_anchor = [
            (e.get("to") or e.get("to_id"), (e.get("timestamp") or ""))
            for e in _edges
            if str((e or {}).get("type") or "").upper() == "LED_TO" and e.get("from") == anchor_id
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
            if str((e or {}).get("type") or "").upper() == "LED_TO" and e.get("to") == anchor_id
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
            et = str((e or {}).get("type") or "").upper()
            if et == "CAUSAL" and (e.get("orientation") == "succeeding") and \
               (e.get("from") == anchor_id or e.get("to") == anchor_id):
                next_id = e.get("to"); break
        # Fallback: un-oriented direct CAUSAL out of anchor (strictly same-domain).
        if not next_id:
            for e in edges:
                if str((e or {}).get("type") or "").upper() == "CAUSAL" and e.get("from") == anchor_id:
                    next_id = e.get("to"); break
        if next_id and next_id in allowed and next_id not in support:
            support.append(next_id)
    except (AttributeError, TypeError, ValueError):
        # Optional pointer — safe to proceed without it
        pass
    return support

# ---------------------------------------------------------------------------
async def _enrich_for_short_answer(ev: WhyDecisionEvidence, policy_headers: dict | None = None) -> None:
    """
    BOUNDED enrich for prose only (≤4 IDs): Top-3 preceding events + first succeeding decision.
    - Calls Memory /api/enrich/batch with the SAME policy headers and snapshot_etag precondition.
    - Attaches lightweight items to ev.events; does NOT mutate edges or widen scope.
    Baseline: Gateway MAY do this for short-answer titles; wire stays edges-only. 
    """
    try:
        from core_config import get_settings as _get_settings
        from core_http.client import get_http_client
        s = _get_settings()
        mem_url = getattr(s, "memory_api_url", None)
        if not mem_url:
            return
        allowed = list(getattr(ev, "allowed_ids", []) or [])
        # Enrich exactly the set we plan to cite (minus the anchor)
        try:
            planned = _compute_cited_ids(ev)
        except Exception:
            planned = []
        anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
        ids = [i for i in planned if i and i != anchor_id and i in allowed][:4]
        if not ids:
            return

        # --- Canonical negative-cache key (evidence:{etag}|{allowed_ids_fp}|{policy_fp}) ---
        try:
            meta = dict(getattr(ev, "meta", {}) or {})
            allowed_ids_fp = str(meta.get("allowed_ids_fp") or "")
            policy_fp = str(meta.get("policy_fp") or (meta.get("fingerprints") or {}).get("policy_fingerprint") or "")
            etag = str(getattr(ev, "snapshot_etag", "") or "")
            ev_cache_key = cache_keys.evidence(etag, allowed_ids_fp, policy_fp)
        except (AttributeError, TypeError, ValueError):
            ev_cache_key = None

        # Short-circuit on recent 412 marker
        if ev_cache_key:
            try:
                rc = get_redis_pool()
                if rc is not None:
                    mark = await rc.get(ev_cache_key)
                    if isinstance(mark, (bytes, str)) and str(mark).startswith('{"_neg412":'):
                        log_stage(
                            logger, "enrich", "neg_cache_412_hit",
                            cache_key=ev_cache_key, request_id=(current_request_id() or "unknown")
                        )
                        return
            except (TypeError, AttributeError, RuntimeError):
                # Redis unavailable or non-awaitable test double – ignore
                pass

        # Call Memory batch-enrich (policy- and snapshot-bound)
        headers = dict(policy_headers or {})
        etag_hdr = getattr(ev, "snapshot_etag", None)
        if etag_hdr:
            headers["X-Snapshot-Etag"] = etag_hdr
        client = get_http_client(timeout_ms=3000)
        payload = {"anchor_id": anchor_id, "snapshot_etag": etag_hdr, "ids": ids}
        r = await client.post(f"{mem_url}/api/enrich/batch", json=payload, headers=headers)
        if hasattr(r, "raise_for_status"):
            try:
                r.raise_for_status()
            except Exception as exc:
                # Handle 412 specifically; otherwise keep enrich optional and return
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 412 and ev_cache_key:
                    try:
                        rc = get_redis_pool()
                        if rc is not None:
                            await rc.setex(ev_cache_key, int(TTL_EVIDENCE_CACHE_SEC), jsonx.dumps({"_neg412": True}))
                        log_stage(
                            logger, "enrich", "neg_cache_412_store",
                            cache_key=ev_cache_key, ttl=int(TTL_EVIDENCE_CACHE_SEC),
                            request_id=(current_request_id() or "unknown")
                        )
                    except (TypeError, AttributeError, RuntimeError):
                        pass
                return
        data = r.json() if hasattr(r, "json") else {}
        items = (data or {}).get("items") or {}
        # Attach minimal enriched items to evidence.events for templater consumption
        cur = list(getattr(ev, "events", []) or [])
        by_id = {d.get("id"): d for d in cur if isinstance(d, dict)}
        for iid in ids:
            node = items.get(iid) or {}
            if not isinstance(node, dict): 
                continue
            mapped = {
                "id": node.get("id") or iid,
                "type": node.get("type") or node.get("entity_type") or "EVENT",
                "title": (node.get("title") or "")[:256],
                "description": (node.get("description") or "")[:512],
                "timestamp": node.get("timestamp") or "",
            }
            if mapped["id"] in by_id:
                by_id[mapped["id"]].update({k: v for k, v in mapped.items() if v})
            else:
                cur.append(mapped)
        ev.__dict__["events"] = cur
        log_stage(logger, "enrich", "batch_applied",
                  count=len(ids), request_id=(current_request_id() or "unknown"))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as _e:
        # Enrich is optional; mark and continue with edges-only rendering
        try:
            setattr(ev, "_enrich_failed", True)
        except (AttributeError, RuntimeError):
            pass
        log_stage(logger, "enrich", "batch_failed",
                  error=type(_e).__name__, request_id=(current_request_id() or "unknown"))

# ---------------------------------------------------------------------------
# Evidence pre-processing helpers (non-normalising)
# ---------------------------------------------------------------------------
def _dedup_and_normalise_events(ev: WhyDecisionEvidence) -> None:
    """Deduplicate events **by id only**, preserving original fields.
    No slugging, no timestamp coercion, no amount parsing (ingest owns that)."""
    try:
        events = list(ev.events or [])
    except Exception:
        return
    def _key(e: dict) -> tuple[str, str]:
        try: return ((e or {}).get("timestamp") or "", (e or {}).get("id") or "")
        except Exception: return ("","")
    seen: set[str] = set(); deduped: list[dict] = []
    for e in sorted([x for x in events if isinstance(x, dict)], key=_key):
        _id = str(e.get("id") or "").strip()
        if not _id or _id in seen:
            continue
        seen.add(_id); deduped.append(e)
    try:
        ev.events = deduped
    except Exception:
        pass
    try:
        from .logging_helpers import stage as log_stage
        log_stage("evidence", "dedup_applied", removed=max(0, len(events) - len(deduped)), kept=len(deduped))
        # Metrics: count duplicate inputs rejected (events)
        try:
            metric_counter("gateway_duplicate_inputs_rejected_total", max(0, len(events) - len(deduped)), kind="event")
        except Exception:
            pass
    except Exception:
        pass

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
        and str(e.get("type") or "").upper() == "ALIAS_OF"
        and (e.get("to") == anchor_id)
    }
    out = []
    pre_n = 0
    suc_n = 0
    for e in edges:
        if not isinstance(e, dict):
            continue
        et = str(e.get("type") or "").upper()
        if et == "ALIAS_OF":
            e.pop("orientation", None)  # neutral
        elif et in ("LED_TO", "CAUSAL"):
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
    log_stage(
        logger, "cache", "store",
        layer="evidence", cache_key=k, ttl=int(TTL_EVIDENCE_CACHE_SEC),
    )

# ───────────────────── main entry-point ─────────────────────────
@trace_span("builder", logger=logger)
async def build_why_decision_response(
    req: "AskIn",                          # forward-declared (defined in app.py)
    evidence_builder,                      # EvidenceBuilder instance (singleton passed from app.py)
    *,
    source: str = "query",                 # "ask" | "query" – used for policy/logging
    fresh: bool = False,                   # bypass caches when gathering evidence
    policy_headers: dict | None = None,    # pass policy headers through to Memory API
    stage_times: dict | None = None,       # STAGE 5: accumulate per-stage latencies (ms)
) -> Tuple[WhyDecisionResponse, Dict[str, bytes], str]:
    """
    Assemble Why-Decision response and audit artifacts.
    Returns (response, artifacts_dict, request_id).
    """
    t0      = time.perf_counter()
    # Prefer inbound request_id from the active context; fall back to req field; else generate.
    try:
        bound_rid = current_request_id()
    except Exception:
        bound_rid = None
    req_id  = getattr(req, "request_id", None) or bound_rid or uuid.uuid4().hex
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

    # Persist deterministic plan inputs; include selector policy id for audits
    plan_dict = {
        "node_id": ev.anchor.id,
        "k": 1,
        "selector_policy_id": _SELECTOR_POLICY_ID,
    }
    artifacts["plan.json"] = jsonx.dumps(plan_dict).encode()

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
    except Exception:
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
    try:
        from opentelemetry import trace as _trace  # type: ignore
        _sp = _trace.get_current_span()
        _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
        _trace_id = f"{_ctx.trace_id:032x}" if _ctx and getattr(_ctx, "trace_id", 0) else None
        _span_id  = f"{_ctx.span_id:016x}" if _ctx and getattr(_ctx, "span_id", 0) else None
    except Exception:
        _trace_id, _span_id = None, None
    _anchor_id = getattr(getattr(ev, "anchor", None), "id", None) or "unknown"
    # Selector audit
    try:
        ranked = _selector.rank_events(ev.anchor, derive_events_from_edges(ev))
        ranked_event_ids = [d.get("id") for d in ranked if isinstance(d, dict)]
        sel_scores = []
    except Exception:
        ranked_event_ids, sel_scores = [], []
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
            except Exception:
                pass
    except Exception as e:
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
    except Exception as e:
        log_stage(logger, "orientation", "apply_exception",
                  request_id=req_id, error=type(e).__name__,
                  anchor_id=_ctx_anchor_id, bundle_fp=_ctx_bundle_fp)
        raise
    artifacts["evidence_post.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()
    # v3: events not attached to evidence; ranking kept internal for meta if needed.

# Build new meta inputs
    _ts_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
    except Exception:
        _stage_latencies_canonical = dict(stage_times or {})
    # Ensure total is present alongside per-stage values
    try:
        _stage_latencies_canonical["total"] = int((time.perf_counter() - t0) * 1000)
    except Exception:
        pass
    # Request-level timeout budget (best-effort)
    try:
        from core_config.constants import TIMEOUT_LLM_MS, TIMEOUT_ENRICH_MS, TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS, TIMEOUT_VALIDATE_MS
        _timeout_ms = max(TIMEOUT_LLM_MS, TIMEOUT_ENRICH_MS, TIMEOUT_SEARCH_MS, TIMEOUT_EXPAND_MS, TIMEOUT_VALIDATE_MS)
    except Exception:
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
            "request_id": req_id,
            "trace_id": _trace_id,
            "span_id": _span_id,
            "ts_utc": _ts_utc,
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
    except Exception:
        policy_trace_val = {}
    # Compute download gating based on actor role; directors/execs may access full bundles.
    try:
        actor_role = (getattr(meta_obj, "actor", {}) or {}).get("role")
    except Exception:
        actor_role = None
    allow_full = str(actor_role).lower() in ("director", "exec")
    downloads_manifest = {
        "artifacts": [
            {
                "name": "bundle_view",
                "allowed": True,
                "reason": None,
                "href": f"/v2/bundles/{req_id}/download?name=bundle_view"
            },
            {
                "name": "bundle_full",
                "allowed": bool(allow_full),
                "reason": None if allow_full else "acl:sensitivity_exceeded",
                "href": f"/v2/bundles/{req_id}/download?name=bundle_full"
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
            except Exception:
                _pfp = None
        # Propagate only if present; do not derive a new fingerprint locally.
        if _pfp:
            meta_dict["policy_fp"] = _pfp
            meta_dict.setdefault("fingerprints", {})["policy_fingerprint"] = _pfp
            log_stage(logger, "builder", "policy_fingerprint", action="propagate", fp=_pfp)
    except Exception:
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

    bundle_url = f"/v2/bundles/{req_id}"
    # Will be computed after final response serialization; initialize for early references.
    bundle_fp_final: str | None = None
    try:
        if not getattr(meta_obj, "resolver_path", None):
            setattr(meta_obj, "resolver_path", "direct")
    except Exception:
        try:
            meta_obj["resolver_path"] = "direct"  # type: ignore[index]
        except Exception:
            pass

    # Strategic: single audit log of pool/prompt/payload cardinalities
    log_stage(logger, "meta", "pool_prompt_payload",
              request_id=req_id,
              pool=len(meta_inputs.evidence_sets.pool_ids),
              prompt=len(meta_inputs.evidence_sets.prompt_included_ids),
              payload=len(meta_inputs.evidence_sets.payload_included_ids))

    # Return response with enriched meta (dict)
    try:
        stage_times['render_response'] = int((time.perf_counter() - _t_render) * 1000)
    except Exception:
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
    except Exception:
        edges = []
    try:
        has_pre = any(str((e or {}).get("orientation") or "").lower() == "preceding" for e in edges)
        has_suc = any(str((e or {}).get("orientation") or "").lower() == "succeeding" for e in edges)
    except Exception:
        has_pre, has_suc = False, False
    try:
        _event_count = len(derive_events_from_edges(ev))
    except Exception:
        _event_count = 0
    flags = CompletenessFlags(
        has_preceding=bool(has_pre),
        has_succeeding=bool(has_suc),
        event_count=_event_count,
    )
    log_stage(logger, "builder", "completeness_flags_type",
              given=f"{flags.__class__.__module__}.{flags.__class__.__name__}",
              request_id=req_id)
    # Optional bounded enrich for short-answer titles (Top-3 + Next). Wire remains edges-only.
    try:
        await _enrich_for_short_answer(ev, policy_headers=policy_headers)
    except Exception:
        # Safe to continue; templater will fall back gracefully.
        pass
    # Build the public short answer from the full evidence; templater handles rendering.
    ans = _build_minimal_answer(ev)

    # Forward-looking shape: WhyDecisionResponse requires top-level anchor/graph.
    # Keep extra fields (e.g., intent, bundle_url) harmlessly via extra="allow".
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
    envelope, bundle_fp_final = _build_exec_summary_envelope(
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
    except Exception as _exc:
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
            "cited_ids_count": int(len(getattr(resp.answer, "cited_ids", []) or [])),
            "cited_ids": list(getattr(resp.answer, "cited_ids", []) or []),
            "templater": {"mode": "deterministic", "template_version": "v3-key-events"},
            "bundle_fp": bundle_fp_final,
        },
        "response_preview": {
            "short_answer": str(getattr(resp.answer, "short_answer", "") or ""),
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
    # Stage-7 validator report (delegates to core validator) and fail-closed.
    from .validator import view_artifacts_allowed
    from .validator import run_validator as _run_validator
    _allowed = view_artifacts_allowed()
    # Include the base trace.json so the "view" bundle meets the schema's required artifacts.
    _artifacts_for_view = {k: v for k, v in artifacts.items() if k in _allowed}
    _val_report = _run_validator(
        envelope,   # validate the **enveloped** response (contains meta.bundle_fp)
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
    except Exception:
        pass
    if not bool(_val_report.get("pass")):
        try:
            log_stage(logger, "validator", "bundle_failed",
                      request_id=req_id,
                      anchor_id=_ctx_anchor_id,
                      bundle_fp=(bundle_fp_final or "unknown"),
                      errors=[e.get("code") for e in (_val_report.get("errors") or [])][:10])
        except Exception:
            pass
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
    except Exception:
        # MinIO not configured / import failed — ignore silently.
        pass
    return resp, artifacts, req_id
