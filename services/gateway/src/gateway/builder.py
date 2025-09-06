from __future__ import annotations
import time, uuid, os, re
from datetime import datetime, timezone
from typing import Any, Tuple, Dict  # Mapping and List are unused

from core_utils import jsonx
from core_config import get_settings

import importlib.metadata as _md
from core_logging import get_logger, trace_span
from .logging_helpers import stage as log_stage
# canonical_json is not used in this module; remove unused import
from core_models.models import (
    WhyDecisionAnchor,
    WhyDecisionAnswer,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    WhyDecisionTransitions,
    CompletenessFlags,
    MetaInfo,
)
from core_models.meta_inputs import MetaInputs
from shared import dedup_and_normalise_events as _shared_dedup_and_normalise_events
from gateway import selector as _selector
from gateway.inference_router import last_call as _inference_last_call
from gateway.inference_router import sanitize_fallback_reason as _sanitize_fallback_reason
from .prompt_envelope import build_prompt_envelope
from .templater import finalise_short_answer
from core_validator import validate_response as _core_validate_response
from gateway.inference_router import llm_call
from core_config.constants import SELECTOR_MODEL_ID
from .load_shed import should_load_shed
import inspect

# Import metadata helpers to construct canonical meta information.  The
# MetaInputs and EvidenceMetrics models define the JSON‑first schema
# for Gateway metadata; build_meta normalises and validates the final
# object.  Keeping these imports local to this module avoids
# introducing dependencies in the public API surface of gateway.builder.
# MetaInputs provides the JSON‑first schema for meta construction.
# Use the shared meta builder to normalise and validate meta information.
from shared.meta_builder import build_meta


logger   = get_logger("gateway.builder")
logger.propagate = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_cited_ids(ev: WhyDecisionEvidence) -> list[str]:
    """Compute deterministic cited_ids for the given evidence.

    Ordering:
      [anchor] + [top 3 events by selector.rank_events] + [preceding first] + [succeeding first]
    Only IDs present in ev.allowed_ids are considered; duplicates removed.
    Set CITE_ALL_IDS=true to bypass and emit ev.allowed_ids in order.
    """
    try:
        s = get_settings()
        cite_all_env = "1" if getattr(s, "cite_all_ids", False) else ""
        cite_all = cite_all_env.lower() in ("true", "1", "yes", "y")
    except Exception:
        cite_all = False
    allowed = list(getattr(ev, "allowed_ids", []) or [])
    if cite_all:
        return allowed
    support: list[str] = []
    # Anchor
    anchor_id = None
    try:
        anchor_id = getattr(ev.anchor, "id", None)
    except Exception:
        anchor_id = None
    if anchor_id and anchor_id in allowed:
        support.append(anchor_id)
    # Events: use deterministic selector ranking, then take the top three
    events: list[dict] = []
    try:
        for e in ev.events or []:
            if isinstance(e, dict):
                ed = e
            else:
                try:
                    ed = e.model_dump(mode="python")  # type: ignore[attr-defined]
                except Exception:
                    ed = dict(e)
            etype = (ed.get("type") or ed.get("entity_type") or "").lower()
            if etype == "event":
                events.append(ed)
    except Exception:
        events = []
    # Rank via shared helper; fallback to timestamp desc if anything goes wrong
    try:
        ranked = _selector.rank_events(ev.anchor, events)[:3]
    except Exception:
        ranked = sorted(events, key=lambda x: (x.get("timestamp") or "", x.get("id") or ""), reverse=True)[:3]
    for evd in ranked:
        eid = evd.get("id")
        if eid and eid in allowed and eid not in support:
            support.append(eid)
    # Preceding transition
    first_pre_id: str | None = None
    try:
        pre_list = list(getattr(ev.transitions, "preceding", []) or [])
    except Exception:
        pre_list = []
    for tr in pre_list:
        tid = tr.get("id") if isinstance(tr, dict) else getattr(tr, "id", None)
        if tid:
            first_pre_id = tid
            break
    if first_pre_id and first_pre_id in allowed and first_pre_id not in support:
        support.append(first_pre_id)
    # Succeeding transition
    first_suc_id = None
    # Prefer citing the succeeding **decision** (transition.to), not the transition edge
    try:
        for tr in list(getattr(ev.transitions, "succeeding", []) or []):
            to_id = tr.get("to") if isinstance(tr, dict) else getattr(tr, "to", None)
            if to_id:
                first_suc_id = to_id
                break
    except Exception:
        pass
    if first_suc_id and first_suc_id in allowed and first_suc_id not in support:
        support.append(first_suc_id)
    return support

# ---------------------------------------------------------------------------
# Evidence pre‑processing helpers
# ---------------------------------------------------------------------------
def _dedup_and_normalise_events(ev: WhyDecisionEvidence) -> None:
    """
    Deduplicate and normalise events on a WhyDecisionEvidence object.

    When evidence is provided directly by callers or tests it may contain
    duplicate events (e.g. multiple instances with the same ID or very
    similar summaries on the same day).  This helper collapses such
    duplicates and attaches normalised monetary amounts to each event.  The
    deduplication logic mirrors the behaviour of the EvidenceBuilder:

      1. Remove duplicate IDs (preserving the first occurrence).
      2. Collapse near-identical events occurring on the same day where the
         textual summary differs only by numeric or currency markers.  A
         canonical key is derived from the date (YYYY‑MM‑DD) and the
         summary/description with all digits, currency symbols and
         punctuation stripped and lower‑cased.  Only one event per key is
         retained.
      3. For each retained event, attempt to parse and attach a
         ``normalized_amount`` and ``currency`` using the shared helper
         from ``gateway.evidence``.  Parsing is best-effort and silent on
         failure.

    The input evidence object is modified in place; no value is returned.
    """
    return _shared_dedup_and_normalise_events(ev)

class _LruTTLCache:
    def __init__(self, max_items: int = 200, ttl_seconds: int = 600):
        from collections import OrderedDict
        self._data: "OrderedDict[str, tuple[float, dict[str, bytes]]]" = OrderedDict()
        self._ttl = max(1, int(ttl_seconds))
        self._cap = max(1, int(max_items))
    def _purge(self, now: float):
        # Drop expired
        keys = [k for k,(ts,_) in list(self._data.items()) if now - ts > self._ttl]
        for k in keys:
            self._data.pop(k, None)
        # Enforce cap
        while len(self._data) > self._cap:
            self._data.popitem(last=False)
    def __setitem__(self, key: str, value: dict[str, bytes]):
        import time as _t
        now = _t.time()
        self._data[key] = (now, value)
        self._data.move_to_end(key)
        self._purge(now)
    def get(self, key: str):
        import time as _t
        now = _t.time()
        item = self._data.get(key)
        if not item:
            self._purge(now)
            return None
        ts, val = item
        if now - ts > self._ttl:
            self._data.pop(key, None)
            self._purge(now)
            return None
        self._data.move_to_end(key)
        return val

import os as _os
_max = int((_os.getenv("BUNDLE_CACHE_MAX_ITEMS") or "200").strip() or "200")
_ttl = int((_os.getenv("BUNDLE_CACHE_TTL_S") or "600").strip() or "600")
BUNDLE_CACHE = _LruTTLCache(max_items=_max, ttl_seconds=_ttl)

try:
    _GATEWAY_VERSION = _md.version("gateway")
except _md.PackageNotFoundError:
    _GATEWAY_VERSION = "unknown"

# ───────────────────── main entry-point ─────────────────────────
@trace_span("builder", logger=logger)
async def build_why_decision_response(
    req: "AskIn",                          # forward-declared (defined in app.py)
    evidence_builder,                      # EvidenceBuilder instance (singleton passed from app.py)
    *,
    source: str = "ask",                   # "ask" | "query" – used for policy/logging
    fresh: bool = False,                   # bypass caches when gathering evidence
    policy_headers: dict | None = None,    # pass policy headers through to Memory API
) -> Tuple[WhyDecisionResponse, Dict[str, bytes], str]:
    """
    Assemble Why-Decision response and audit artefacts.
    Returns (response, artefacts_dict, request_id).
    """
    t0      = time.perf_counter()
    req_id  = req.request_id or uuid.uuid4().hex
    arte: Dict[str, bytes] = {}
    settings = get_settings()

    # ── evidence (k = 1 collect) ───────────────────────────────
    ev: WhyDecisionEvidence
    if req.evidence is not None:
        ev = req.evidence
    elif req.anchor_id:
        # Pass policy headers through to EvidenceBuilder → Memory API
        maybe = evidence_builder.build(req.anchor_id, fresh=fresh, policy_headers=policy_headers)
        ev = await maybe if inspect.isawaitable(maybe) else maybe
        if ev is None:

            ev = WhyDecisionEvidence(
                anchor=WhyDecisionAnchor(id=req.anchor_id),
                events=[],
                transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            )
            ev.snapshot_etag = "unknown"
    else:                       # safeguard – should be caught by AskIn validator
        ev = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id="unknown"),
            events=[],
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
        )
    # Preprocess the evidence events to remove duplicates and normalise
    # monetary amounts.  This mirrors the EvidenceBuilder logic when
    # callers provide their own evidence stubs.  See tests covering
    # deduplication and amount normalisation for rationale.
    try:
        if req.evidence is not None:
            _dedup_and_normalise_events(ev)
    except Exception:
        pass

    # Omit empty transition lists from the evidence by converting them to None
    try:
        if not (getattr(ev.transitions, "preceding", []) or []):
            ev.transitions.preceding = None  # type: ignore
        if not (getattr(ev.transitions, "succeeding", []) or []):
            ev.transitions.succeeding = None  # type: ignore
    except Exception:
        pass
    arte["evidence_pre.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()

    # ── deterministic plan stub (needed for audit contract) ────────────
    plan_dict = {"node_id": ev.anchor.id, "k": 1}
    # Store the deterministic plan stub as bytes for artefacts persistence.
    arte["plan.json"] = jsonx.dumps(plan_dict).encode()

    # ---- Gate: single budgeting authority (renders messages + max_tokens) ----
    # We compute canonical allowed_ids first to give the gate a stable envelope,
    # then re-canonicalise after trimming to remove IDs for dropped evidence.
    try:
        ev_events = []
        for e in (ev.events or []):
            if isinstance(e, dict):
                ev_events.append(e)
            else:
                try:
                    ev_events.append(e.model_dump(mode="python"))
                except Exception:
                    ev_events.append(dict(e))
        ev_trans = []
        for t in list(getattr(ev.transitions, "preceding", []) or []) + list(getattr(ev.transitions, "succeeding", []) or []):
            if isinstance(t, dict):
                ev_trans.append(t)
            else:
                try:
                    ev_trans.append(t.model_dump(mode="python"))
                except Exception:
                    ev_trans.append(dict(t))
    except Exception as e:
        log_stage("builder", "allowed_ids_canonicalization_failed",
                  error=str(e), request_id=getattr(req, "request_id", None))
        raise
    # Strategic logging: how many neighbor *decision* ids made it into allowed_ids
    try:
        _base_ids = set(
            [
                getattr(ev.anchor, "id", None),
                *[
                    (e.get("id") if isinstance(e, dict) else getattr(e, "id", None))
                    for e in (ev_events or [])
                ],
                *[
                    (t.get("id") if isinstance(t, dict) else getattr(t, "id", None))
                    for t in (ev_trans or [])
                ],
            ]
        )
        _neighbor_count = len([x for x in (ev.allowed_ids or []) if x and x not in _base_ids])
        log_stage(
            "builder",
            "allowed_ids_neighbor_decisions",
            request_id=req_id,
            neighbor_decision_ids=_neighbor_count,
            allowed_ids=len(ev.allowed_ids or []),
        )
    except Exception:
        pass
    # Persist canonicalised, pre-gate evidence (post-dedupe, pre-trim)
    arte["evidence_canonical.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()
    try:
        log_stage("builder", "evidence_final_persisted",
                  request_id=req_id,
                  allowed_ids=len(getattr(ev, "allowed_ids", []) or []))
    except Exception:
        pass

    # Build a pre-envelope (the gate will strip evidence when computing overhead)
    # Warm the policy registry via unified schema cache (P1: collapse schema fetching)
    try:
        from core_config import get_settings as _get_settings
        s = _get_settings()
        registry_url = getattr(s, "policy_registry_url", None)
        if registry_url:
            from .schema_cache import (
                fetch_policy_registry as _fetch_policy_registry,
                get_cached as _get_cached,
            )
            # See if we already have a cached policy registry; if not, fetch it.
            _cached, _ = _get_cached("/api/policy/registry")
            if _cached is None:
                await _fetch_policy_registry()
                try:
                    log_stage("schema", "policy_registry_warmed", request_id=req_id, url=registry_url)
                except Exception:
                    pass
            else:
                try:
                    log_stage("schema", "policy_registry_cache_hit", request_id=req_id)
                except Exception:
                    pass
        else:
            try:
                log_stage("schema", "policy_registry_warm_skipped", request_id=req_id)
            except Exception:
                pass
    except Exception:
        try:
            # Do not reference registry_url here – it may not be set on exceptions.
            log_stage("schema", "policy_registry_warm_failed", request_id=req_id)
        except Exception:
            pass
    pre_envelope = build_prompt_envelope(
        question=f"Why was decision {ev.anchor.id} made?",
        # Pass only non‑None fields in the evidence bundle to avoid including
        # empty transition arrays.  This helps the gate compute a prompt
        # envelope consistent with the public API contract.
        evidence=ev.model_dump(mode="python", exclude_none=True),
        snapshot_etag=getattr(ev, "snapshot_etag", "unknown"),
        intent=req.intent,
        endpoint=source,
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )
    from gateway.budget_gate import run_gate as _run_gate
    gate_plan, trimmed_evidence = _run_gate(pre_envelope, ev, request_id=req_id, model_name=None)
    try:
        ev_prompt = trimmed_evidence if isinstance(trimmed_evidence, WhyDecisionEvidence) \
             else WhyDecisionEvidence.model_validate(trimmed_evidence)
        # Normalise empty transition lists on the prompt copy only (omit null arrays in prompt JSON)
        try:
            if not (getattr(ev_prompt.transitions, "preceding", []) or []):
                ev_prompt.transitions.preceding = None  # type: ignore
            if not (getattr(ev_prompt.transitions, "succeeding", []) or []):
                ev_prompt.transitions.succeeding = None  # type: ignore
        except Exception:
            pass
    except Exception as e:
        log_stage("builder", "prompt_evidence_prepare_failed",
                  error=str(e), request_id=req_id)
        ev_prompt = ev  # safe fallback
    # Normalise empty transition lists to None before serialising the post-gate evidence.
    # When a field is None Pydantic can omit it from the JSON (exclude_none=True).
    try:
        if not (getattr(ev.transitions, "preceding", []) or []):
            ev.transitions.preceding = None  # type: ignore
        if not (getattr(ev.transitions, "succeeding", []) or []):
            ev.transitions.succeeding = None  # type: ignore
    except Exception:
        pass
    arte["evidence_post.json"] = jsonx.dumps(ev.model_dump(mode="python", exclude_none=True)).encode()
    # ── Events Policy E: show a few, keep all (response up to 10; count & truncate flags) ──
    try:
        _full_events_for_policy = list(ev.events or [])
    except Exception:
        _full_events_for_policy = []
    try:
        _ranked_all = _selector.rank_events(ev.anchor, _full_events_for_policy)
    except Exception:
        _ranked_all = sorted(
            _full_events_for_policy, key=lambda x: (x.get("timestamp") or "", x.get("id") or ""), reverse=True
        )
    _events_total = len(_ranked_all)
    _events_truncated_flag = _events_total > 10
    # Keep the first 10 events with full detail; append id-only stubs for the rest.
    head = _ranked_all[:10]
    tail_ids = [e.get("id") if isinstance(e, dict) else getattr(e, "id", None) for e in _ranked_all[10:]]
    tail = [{"id": tid} for tid in tail_ids if tid]
    ev.events = head + tail
    # Rebuild allowed_ids after shaping events via the canonical helper
    try:
        from core_validator import canonical_allowed_ids as _canon
        aid = getattr(ev.anchor, "id", "") or ""
        ev_events = [e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python") for e in (ev.events or [])]
        ev_trans = []
        tr = getattr(ev, "transitions", None)
        if tr is not None:
            ev_trans.extend(getattr(tr, "preceding", []) or [])
            ev_trans.extend(getattr(tr, "succeeding", []) or [])
            ev_trans = [t if isinstance(t, dict) else getattr(t, "model_dump", dict)(mode="python") for t in ev_trans]
        ev.allowed_ids = _canon(aid, ev_events, ev_trans)
        try:
            log_stage('builder','allowed_ids_preserved', request_id=req_id, count=len(ev.allowed_ids or []))
        except Exception:
            pass
    except Exception:
        pass
    # Extract selector/gate metrics (if provided by selector)
    sel_meta = {}
    try:
        for entry in (gate_plan.logs or []):
            if "selector_truncation" in entry:
                sel_meta = entry
                break
    except Exception:
        sel_meta = {}

    # ── canonical prompt envelope + fingerprint ────────────────
    envelope = build_prompt_envelope(
        question=f"Why was decision {ev.anchor.id} made?",
        evidence=ev_prompt.model_dump(mode="python", exclude_none=True),
        snapshot_etag=getattr(ev, "snapshot_etag", "unknown"),
        endpoint=source,
        intent=req.intent,
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )
    # Fingerprints (single source of truth for prompt + bundle)
    prompt_fp: str = (gate_plan.fingerprints or {}).get("prompt") or "unknown"
    bundle_fp: str = envelope.get("_fingerprints", {}).get("bundle_fingerprint") or "unknown"
    snapshot_etag_fp: str = envelope.get("_fingerprints", {}).get("snapshot_etag") or "unknown"
    try:
        log_stage("meta", "fingerprints", request_id=req_id,
                  prompt_fp=prompt_fp, bundle_fp=bundle_fp, snapshot_etag=snapshot_etag_fp)
    except Exception:
        pass

    # ── answer generation with JSON‑only LLM and deterministic fallback ──
    raw_json: str | None = None
    llm_fallback = False
    retry_count = 0
    ans: WhyDecisionAnswer | None = None
    fallback_reason: str | None = None

    if req.answer is not None:
        # If the caller provided an answer already, skip LLM invocation.
        ans = req.answer
    else:
        settings = get_settings()
        # Endpoint-aware LLM gating: ask/query may override the global llm_mode.
        # Values: off|on|auto (auto is treated as on unless load-shed)
        try:
            effective_mode = (settings.ask_llm_mode if source == "ask" else settings.query_llm_mode) or settings.llm_mode
        except Exception:
            effective_mode = getattr(settings, "llm_mode", "off")
        use_llm = ((effective_mode or "off").lower() != "off") and (not should_load_shed())
        # Strategic log (B5 envelope): makes the gate visible in traces & audit
        try:
            log_stage("prompt", "llm_gate",
                      llm_mode=getattr(settings, "llm_mode", None),
                      endpoint_mode=effective_mode,
                      source=source,
                      load_shed=should_load_shed(),
                      use_llm=use_llm,
                      policy_model=getattr(settings, "vllm_model_name", None))
            if not use_llm and should_load_shed():
                # Strategic breadcrumb for the Audit Drawer
                log_stage("llm", "shed", reason="load_shed_flag", request_id=req_id)
        except Exception:
            pass
        # If the LLM is disabled by endpoint/global config, record & mark fallback.
        if not use_llm:
            try:
                log_stage("llm", "disabled",
                    request_id=req_id,
                    llm_mode=effective_mode,
                    source=source,
                    reason="llm_mode_off",
                )
            except Exception:
                pass
            # When LLM is disabled we always synthesize a fallback answer
            llm_fallback = True
            # Set explicit reason for fallback
            fallback_reason = "llm_off"
        if use_llm:
            # Determine retry count from the policy registry, capped at 2
            try:
                policy_cfg = envelope.get("policy", {}) or {}
                policy_retries = int(policy_cfg.get("retries", 0))
            except Exception:
                policy_retries = 0
            max_retries = min(policy_retries, 2)
            # Temperature and max_tokens from the envelope
            try:
                temp = float(policy_cfg.get("temperature", 0.0))
            except Exception:
                temp = 0.0
            # Gate is authoritative for completion; router will only safety-clamp.
            max_tokens = int(gate_plan.max_tokens or envelope.get("constraints", {}).get("max_tokens", 256))
            try:
                raw_json = await llm_call(
                    envelope,
                    request_id=req_id,
                    headers=None,
                    retries=max_retries,
                    temperature=temp,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                raw_json = None
                # Leave detailed reason mapping to inference_router; we just mark the attempt.
                try:
                    log_stage(
                        "inference",
                        "dispatch_exception",
                        request_id=req_id,
                        exception=type(e).__name__,
                    )
                except Exception:
                    pass
            # No explicit retry loop here; llm_call delegates retries.
            retry_count = max_retries
        # Determine whether this is a fallback: if the LLM did not run
        # (use_llm is false) or summarise_json returned no result.
        if raw_json is None:
            # The LLM did not run or returned no result.  Always mark this as a fallback
            # because a deterministic answer must be synthesised.  Preserve any
            # previously set fallback_reason (e.g. "llm_off") and use
            # "no_raw_json" when the LLM was expected to run.
            llm_fallback = True
            if use_llm:
                try:
                    from gateway.inference_router import last_call as imported_last_call  # modern source of truth
                except Exception:
                    imported_last_call = {}
                error_code = imported_last_call.get("error_code") if isinstance(imported_last_call, dict) else None
                if not error_code:
                    status = (str(imported_last_call.get("status") or "").lower()
                              if isinstance(imported_last_call, dict) else "")
                    if "timeout" in status:
                        error_code = "timeout"
                    elif "http" in status:
                        error_code = "http_error"
                fallback_reason = _sanitize_fallback_reason(error_code)
                # Strategic log: immediate fallback decision (+ environment for audit).
                # When no error code is present default to ``llm_unavailable`` so
                # clients know the model was unreachable (replaces “no_raw_json”).
                try:
                    log_stage(
                        "llm",
                        "fallback_decision",
                        request_id=req_id,
                        reason=(fallback_reason or "llm_unavailable"),
                        adapter=imported_last_call.get("adapter"),
                        endpoint=(imported_last_call.get("endpoint") or getattr(settings, "control_model_endpoint", "")),
                        latency_ms=imported_last_call.get("latency_ms"),
                        policy_model=(envelope.get("policy") or {}).get("model"),
                        openai_disabled=getattr(settings, "openai_disabled", False),
                        canary_pct=getattr(settings, "canary_pct", 0),
                        control=getattr(settings, "control_model_endpoint", ""),
                        canary=getattr(settings, "canary_model_endpoint", ""),
                    )
                except Exception:
                    pass
                if not fallback_reason:
                    # Default to a clear unavailability marker rather than the opaque
                    # legacy “no_raw_json”.
                    fallback_reason = "llm_unavailable"
            else:
                # No LLM expected; if no explicit reason already set assign ``llm_off``
                if not fallback_reason:
                    fallback_reason = "llm_off"
            # Same richer cited_ids logic for the “no raw_json” branch
            # Compute cited ids deterministically
            try:
                support = _compute_cited_ids(ev)
            except Exception:
                support = []
                try:
                    aid = getattr(ev.anchor, "id", None)
                    if aid:
                        support.append(aid)
                except Exception:
                    support = []
            ans = WhyDecisionAnswer(short_answer="", cited_ids=support)
            arte["llm_raw.json"] = b"{}"
        else:
            arte["llm_raw.json"] = raw_json.encode()
            try:
                parsed = jsonx.loads(raw_json)
                ans = WhyDecisionAnswer.model_validate(parsed)
                # If summarise_json returned a deterministic stub answer,
                # mark this as a fallback.  Stub answers begin with
                # "STUB ANSWER:" in the short_answer field.
                if use_llm and isinstance(ans.short_answer, str) and ans.short_answer.startswith("STUB ANSWER"):
                    llm_fallback = True
                    fallback_reason = "stub_answer"
                    try:
                        log_stage("llm", "fallback",
                            request_id=req_id,
                            reason="stub_answer",
                            snapshot_etag=snapshot_etag_fp,
                        )
                    except Exception:
                        pass
            except Exception as e:
                # Parsing or validation failed – treat as a fallback.  Leave the
                # short answer empty so the templater can synthesise a deterministic
                # fallback.  Populate cited_ids based on the anchor or allowed_ids.
                llm_fallback = True
                fallback_reason = "parse_error"
                try:
                    log_stage("llm", "fallback",
                        request_id=req_id,
                        reason="parse_error",
                        detail=str(e)[:200],
                        snapshot_etag=snapshot_etag_fp,
                    )
                except Exception:
                    pass
                # Build richer cited_ids from evidence (anchor + events + transitions),
                # keeping ordering compatible with allowed_ids and deduping.
                try:
                    support = _compute_cited_ids(ev)
                except Exception:
                    support = []
                    try:
                        aid = getattr(ev.anchor, "id", None)
                        if aid:
                            support.append(aid)
                    except Exception:
                        support = []

                ans = WhyDecisionAnswer(short_answer="", cited_ids=support)
                arte["llm_raw.json"] = b"{}"

    changed_support = False
    templater_errs: list[str] = []

    # Safety clamp moved to inference_router._safety_clamp (single clamp policy)

    # Count only atomic events (exclude neighbor decisions)
    def _etype(x):
        try:
            return (x.get("type") or x.get("entity_type") or "").lower()
        except AttributeError:
            return ""

    flags = CompletenessFlags(
        has_preceding=bool(ev.transitions.preceding),
        has_succeeding=bool(ev.transitions.succeeding),
        event_count=sum(1 for e in (ev.events or []) if _etype(e) == "event"),
    )

    # Build preliminary response for validation
    # --- LLM call metadata (if available) ------------------------------------
    llm_model   = None
    llm_canary  = None
    llm_attempt = None
    llm_latency = None
    try:
        llm_model   = _inference_last_call.get("model")
        llm_canary  = _inference_last_call.get("canary")
        llm_attempt = _inference_last_call.get("attempt")
        llm_latency = _inference_last_call.get("latency_ms")
    except Exception:
        pass

    # --- Strategic logging on pivotal decisions ------------------------------
    if llm_fallback:
        try:
            from .metrics import counter as _metric_counter
            _metric_counter('gateway_llm_fallback_total', 1)
        except Exception:
            pass
        log_stage(
            "builder",
            "llm_fallback_used",
            request_id=req_id,
            prompt_fp=prompt_fp,
            bundle_fp=bundle_fp,
            fallback_reason=fallback_reason,
            llm_mode=getattr(settings, "llm_mode", "unknown"),
            load_shed=should_load_shed(),
            llm_model=llm_model,
            llm_canary=llm_canary,
            llm_attempt=llm_attempt,
            llm_latency_ms=llm_latency
        )
    if sel_meta.get("selector_truncation"):
        log_stage("builder", "selector_truncated",
                  request_id=req_id,
                  prompt_tokens=sel_meta.get("prompt_tokens"),
                  max_prompt_tokens=sel_meta.get("max_prompt_tokens"))
    _policy_pre = envelope.get("policy") or {}
    _policy_id_pre = _policy_pre.get("policy_id") or envelope.get("policy_id") or "unknown"
    _prompt_id_pre = envelope.get("prompt_id") or "unknown"
    try:
        _gw_version_pre = os.getenv("GATEWAY_VERSION", _GATEWAY_VERSION)
    except Exception:
        _gw_version_pre = _GATEWAY_VERSION
    _sel_metrics_pre = {}
    try:
        for entry in (gate_plan.logs or []):
            if "selector_truncation" in entry:
                _sel_metrics_pre = entry
                break
    except Exception:
        _sel_metrics_pre = {}

    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=MetaInfo(
            request={},
            policy={},
            budgets={},
            fingerprints={},
            evidence_counts={},
            evidence_sets={},
            selection_metrics={"ranking_policy": selector_policy, "scores": sel_scores},
            truncation_metrics={},
            runtime={},
            validator={},
            load_shed=False,
        ),
    )
    # Validate and normalise the response using the core validator
    # Invoke the validator via the module's global namespace to honour monkey‑patching of
    # ``gateway.builder.validate_response`` in tests.  When tests set
    # gateway.builder.validate_response = <stub>, this call will resolve to the
    # patched function.  Fallback to the core implementation if not found.
    _validator_func = globals().get("validate_response", _core_validate_response)  # type: ignore[name-defined]
    ok, validator_errs = _validator_func(resp)
    # Emit a single aggregated log of unknown keys stripped, per request
    try:
        _unk_anchor, _unk_event, _unk_trans = set(), set(), set()
        for _e in (validator_errs or []):
            _code = _e.get("code"); _det = _e.get("details") or {}; _rem = _det.get("removed_keys") or []
            if _code == "unknown_anchor_keys_stripped":     _unk_anchor.update(_rem)
            elif _code == "unknown_event_keys_stripped":    _unk_event.update(_rem)
            elif _code == "unknown_transition_keys_stripped": _unk_trans.update(_rem)
        if _unk_anchor or _unk_event or _unk_trans:
            log_stage("validator", "unknown_fields_stripped",
                      anchor=sorted(_unk_anchor), event=sorted(_unk_event), transition=sorted(_unk_trans))
        # Post-validation presence telemetry to pinpoint field-loss regressions
        try:
            _a = getattr(resp.evidence, "anchor", None)
            _ad = _a.model_dump(mode="python") if hasattr(_a, "model_dump") else (dict(_a) if isinstance(_a, dict) else {})
            log_stage("validator", "anchor_fields_presence_post",
                      request_id=req_id,
                      has_option=bool(_ad.get("option")),
                      has_rationale=bool(_ad.get("rationale")),
                      has_timestamp=bool(_ad.get("timestamp")),
                      has_decision_maker=bool(_ad.get("decision_maker")))
        except Exception:
            pass
    except Exception:
        pass
    # Post-process the short answer to replace stubs and enforce length
    # Determine if a permission note should be appended based on policy_trace
    _perm_note = False
    try:
        _pt = getattr(ev, "_policy_trace", {}) or {}
        _reasons = dict(_pt.get("reasons_by_id") or {})
        _perm_note = any((str(v).startswith("acl:")) for v in _reasons.values())
    except Exception:
        _perm_note = False
    ans, finalise_changed = finalise_short_answer(resp.answer, resp.evidence, append_permission_note=_perm_note)
    # Recompute cited_ids after finalising answer
    try:
        if ans is not None and (not llm_fallback):
            ans.cited_ids = _compute_cited_ids(ev)
            try:
                log_stage("builder", "cited_ids_recomputed_for_llm", request_id=req_id, count=len(getattr(ans, "cited_ids", []) or []))
            except Exception:
                pass
    except Exception:
        pass
    # Combine all error messages.  Structured errors originate from the core
    # validator.  Legacy templater string errors are no longer appended.
    errs: list = []
    if validator_errs:
        errs.extend(validator_errs)
    if errs:
        log_stage("builder", "validator_repaired",
                  request_id=req_id, error_count=len(errs),
                  snapshot_etag=snapshot_etag_fp)

    # ── persist artefacts ───────────────────────────────────────────
    arte["envelope.json"] = jsonx.dumps(envelope).encode()
    # Build a concise validator report summarising unknown keys removed.
    _unk_anchor_r: set[str] = set()
    _unk_event_r: set[str] = set()
    _unk_trans_r: set[str] = set()
    for _ve in (errs or []):
        _code = _ve.get("code")
        _det = _ve.get("details") or {}
        _rem = _det.get("removed_keys") or []
        if _code == "unknown_anchor_keys_stripped":
            _unk_anchor_r.update(_rem)
        elif _code == "unknown_event_keys_stripped":
            _unk_event_r.update(_rem)
        elif _code == "unknown_transition_keys_stripped":
            _unk_trans_r.update(_rem)
    _validator_report: Dict[str, Any] = {}
    if _unk_event_r:
        _validator_report["unknown_event_keys_removed"] = sorted(_unk_event_r)
    if _unk_trans_r:
        _validator_report["unknown_transition_keys_removed"] = sorted(_unk_trans_r)
    if _unk_anchor_r:
        _validator_report["unknown_anchor_keys_removed"] = sorted(_unk_anchor_r)
    arte["validator_report.json"] = jsonx.dumps(_validator_report).encode()
    arte.setdefault("llm_raw.json", b"{}")

    # Determine gateway version with environment override
    import os as _os
    gw_version = os.getenv("GATEWAY_VERSION", _GATEWAY_VERSION)

    # Determine fallback_used: true if templater/stub used OR **fatal** validation issues
    # Fatal per spec: JSON parse/schema failure, cited_ids ⊄ allowed_ids, missing mandatory IDs
    fatal_codes = {
        "LLM_JSON_INVALID",
        "schema_error",
        "cited_ids_removed_invalid",
        "cited_ids_missing_anchor",
    }
    fatal_validation = any((e.get("code") in fatal_codes) for e in (validator_errs or []))
    any_validation   = bool(validator_errs)
    fallback_used = bool(llm_fallback or any_validation)
    # Log an explicit fallback reason for traceability
    try:
        if not llm_fallback and any_validation:
            if fatal_validation:
                codes = [e.get("code") for e in validator_errs or [] if e.get("code") in fatal_codes]
                log_stage("builder", "validator_fallback",
                          request_id=req_id, codes=codes,
                          snapshot_etag=snapshot_etag_fp)
            else:
                non_fatal = [e.get("code") for e in validator_errs if e.get("code") not in fatal_codes]
                log_stage("builder", "validator_repaired_nonfatal",
                          request_id=req_id, codes=non_fatal,
                          snapshot_etag=snapshot_etag_fp)
    except Exception:
        pass

    _policy = envelope.get("policy") or {}
    _policy_id = _policy.get("policy_id") or envelope.get("policy_id") or "unknown"
    _prompt_id = envelope.get("prompt_id") or "unknown"
    fallback_reason_clean = fallback_reason if fallback_reason is not None else None

    # Clean and flatten evidence metrics; summarise validator errors via count.
    cleaned_metrics: Dict[str, Any] = {}
    try:
        cleaned_metrics = dict(sel_meta or {})
        for rm in ("prompt_tokens", "max_prompt_tokens", "bundle_size_bytes", "overhead_tokens", "prompt_tokens_overhead", "selector_model_id"):
            cleaned_metrics.pop(rm, None)
    except Exception:
        cleaned_metrics = sel_meta or {}

    # ---- New canonical meta (Pool vs Prompt vs Payload) ----
    # Pool = ev.allowed_ids; Prompt = IDs from gate trim meta; Payload = IDs serialized in response (ev)
    from core_validator import canonical_allowed_ids as _canon_ids
    _anchor_id = getattr(ev.anchor, "id", None) or "unknown"
    _pool_ids  = list(getattr(ev, "allowed_ids", []) or [])
    # prompt ids come from gate meta (authoritative for what LLM sees)
    _prompt_ids = list((cleaned_metrics or {}).get("prompt_included_ids") or [])
    # payload ids recomputed from the evidence we will serialize
    try:
        _payload_ids = _canon_ids(_anchor_id,
                                  [e if isinstance(e, dict) else e.model_dump(mode="python") for e in (ev.events or [])],
                                  (getattr(ev.transitions, "preceding", []) or []) + (getattr(ev.transitions, "succeeding", []) or []))
    except Exception:
        _payload_ids = _pool_ids[:]

    # Classify validator outputs into warnings vs errors for accurate reporting
    WARNING_CODES = {
        "unknown_event_keys_stripped",
        "unknown_transition_keys_stripped",
        "unknown_anchor_keys_stripped",
        "timestamp_normalised",
        "tags_normalised",
    }
    _warning_codes_present = sorted({e.get("code") for e in (errs or []) if e.get("code") in WARNING_CODES})
    _error_count = sum(1 for e in (errs or []) if e.get("code") not in WARNING_CODES)
    try:
        log_stage("validator", "counts", request_id=req_id, error_count=_error_count, warnings=_warning_codes_present)
    except Exception:
        pass

    # ---- Trace correlation (trace_id/span_id) ----
    _trace_id = None
    _span_id = None
    try:
        from core_observability.otel import current_trace_id_hex as _cur_tid
        _trace_id = _cur_tid()
    except Exception:
        _trace_id = None
    if not _trace_id:
        try:
            from core_logging import current_trace_ids
            _trace_id, _span_id = current_trace_ids()
        except Exception:
            _trace_id, _span_id = None, None
    try:
        if _trace_id and not _span_id:
            _span_id = _trace_id[:16]
    except Exception:
        pass

    # Counts (typed)
    def _counts(ids: list[str], ev_obj) -> dict:
        try:
            pre = list(getattr(ev_obj.transitions, "preceding", []) or [])
            suc = list(getattr(ev_obj.transitions, "succeeding", []) or [])
            events_n = len(getattr(ev_obj, "events", []) or [])
            trans_n = len(pre) + len(suc)
            total = len(ids or [])
            anchor_n = 1 if _anchor_id else 0
            neighbors_n = max(total - (anchor_n + events_n + trans_n), 0)
            return {
                "anchor": anchor_n,
                "events": events_n,
                "transitions": trans_n,
                "neighbors": neighbors_n,
                "total": total,
            }
        except Exception:
            total = len(ids or [])
            anchor_n = 1 if _anchor_id else 0
            neighbors_n = max(total - (anchor_n + 0 + 0), 0)
            return {"anchor": anchor_n, "events": 0, "transitions": 0, "neighbors": neighbors_n, "total": total}

    # Selection metrics
    try:
        from gateway.selector import rank_events as _rank
        ranked = _rank(ev.anchor, [e if isinstance(e, dict) else e.model_dump(mode="python") for e in (ev.events or [])])
        ranked_event_ids = [d.get("id") for d in ranked if isinstance(d, dict)]
        selector_policy = "sim_desc__ts_iso_desc__id_asc"
    except Exception:
        ranked_event_ids = []
        selector_policy = SELECTOR_MODEL_ID

    # Gate logs → truncation passes
    passes = []
    try:
        for entry in (gate_plan.logs or []):
            step = entry.get("step")
            action = "render" if step in ("render", "render_retry") else "rank_and_trim"
            passes.append({
                "prompt_tokens": int(entry.get("prompt_tokens", 0)),
                "max_prompt_tokens": int(entry.get("max_prompt_tokens", 0)) if entry.get("max_prompt_tokens") is not None else None,
                "action": "render" if step in ("render", "render_retry") else "rank_and_trim",
            })
    except Exception:
        pass

    # M6 — Selector confidence & explainability
    # Use prompt_included_ids when available; otherwise fall back to pool_ids.
    try:
        _candidate_ids = list(_prompt_ids or _pool_ids or [])
    except Exception:
        _candidate_ids = []
    _event_docs_by_id: Dict[str, dict] = {}
    try:
        for _e in (ev_events or []):
            _eid = _e.get("id") if isinstance(_e, dict) else getattr(_e, "id", None)
            if _eid and (_candidate_ids and _eid in _candidate_ids):
                _event_docs_by_id[_eid] = _e if isinstance(_e, dict) else getattr(_e, "model_dump", dict)(mode="python")
    except Exception:
        _event_docs_by_id = {}
    try:
        sel_scores = _selector.compute_scores(ev.anchor, list(_event_docs_by_id.values()))
    except Exception:
        sel_scores = {}
    try:
        log_stage("selector", "metrics", request_id=req_id, ranking_policy=selector_policy, scored=len(sel_scores))
    except Exception:
        pass
# Build new meta inputs
    _ts_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
            "llm": {"mode": (settings.ask_llm_mode if source == "ask" else settings.query_llm_mode) or "off",
                    "model": getattr(_inference_last_call, "get", lambda *_: None)("model")},
            "gateway_version": gw_version, "selector_policy_id": selector_policy,
            "env": {"cite_all_ids": bool(getattr(settings, "cite_all_ids", False)),
                    "load_shed": bool(getattr(settings, "load_shed_enabled", False))}
        },
        budgets={
            "context_window": int(getattr(settings, "vllm_max_model_len", 1920)),
            "desired_completion_tokens": int(getattr(settings, "llm_max_tokens", 256)),
            "guard_tokens": int(getattr(settings, "control_prompt_guard_tokens", 32)) if hasattr(settings, "control_prompt_guard_tokens") else 32,
            "overhead_tokens": int(getattr(gate_plan, "overhead_tokens", 0)),
        },
        fingerprints={"prompt_fp": prompt_fp, "bundle_fp": bundle_fp, "snapshot_etag": snapshot_etag_fp},
        evidence_counts={
            # Pool: everything discovered (k=1 expansions etc.) and allowed
            "pool": _counts(_pool_ids, ev),
            # Prompt: only what actually made it into the prompt after token budgeting
            "prompt_included": _counts(_prompt_ids or _pool_ids, trimmed_evidence if _prompt_ids else ev),
            # Payload: what is serialized back to client
            "payload_serialized": _counts(_payload_ids, ev),
        },
        evidence_sets={
            "pool_ids": _pool_ids,
            # IDs included in the prompt (post-trim). If no trim happened, equals pool.
            "prompt_included_ids": _prompt_ids or _pool_ids,
            # Structured reasons from budget gate (e.g., {"id": "...", "reason": "token_budget"})
            "prompt_excluded_ids": (cleaned_metrics or {}).get("prompt_excluded_ids", []),
            "payload_included_ids": _payload_ids,
            "payload_excluded_ids": [],
            "payload_source": "pool",  # LLM mode is off; payloads are sourced from pool
        },
        # Keep ranked_event_ids for audit; scores can be added later by selector when available
        selection_metrics={"ranking_policy": selector_policy, "ranked_event_ids": ranked_event_ids, "scores": {}},
        truncation_metrics={
            "passes": passes,
            "selector_truncation": bool((cleaned_metrics or {}).get("selector_truncation")),
            "prompt_selector_truncation": bool((cleaned_metrics or {}).get("prompt_selector_truncation")),
        },
        runtime={
            "latency_ms_total": int((time.perf_counter() - t0) * 1000),
            "stage_latencies_ms": {},
            "fallback_used": bool(llm_fallback),
            "fallback_reason": fallback_reason_clean,
            "retries": int(retry_count),
        },
        validator={"error_count": _error_count, "warnings": _warning_codes_present},
        load_shed=False,
    )

    # Build the canonical MetaInfo once; idempotent by design.
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
    meta_dict = meta_obj.model_dump()
    meta_dict["policy_trace"] = policy_trace_val
    meta_dict["downloads"] = downloads_manifest
    # Populate payload_excluded_ids from policy_trace (acl/redaction guards)
    try:
        _reasons = dict(policy_trace_val.get("reasons_by_id") or {})
        _payload_excl = [{"id": k, "reason": v} for k, v in _reasons.items() if isinstance(v, str) and v.startswith("acl:")]
        meta_dict.setdefault("evidence_sets", {}).setdefault("payload_excluded_ids", [])
        meta_dict["evidence_sets"]["payload_excluded_ids"] = _payload_excl
        if _payload_excl:
            log_stage("builder", "payload_exclusions_recorded", request_id=req_id, count=len(_payload_excl))
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
        log_stage(
            "builder",
            "etag_propagated",
            anchor_id=anchor_id,
            snapshot_etag=ev_etag,
        )
    except Exception:
        pass

    bundle_url = f"/v2/bundles/{req_id}"

    try:
        BUNDLE_CACHE[req_id] = dict(arte)
    except Exception:
        # In the unlikely event we cannot cache the bundle, proceed
        # without raising an error; the GET endpoint will return 404.
        pass

    # Optionally persist artefacts to MinIO without blocking the request path.
    try:
        # Lazy-import to avoid hard runtime dep in tests/local.
        from gateway.app import _minio_put_batch_async as _minio_save
        import asyncio as _asyncio
        _asyncio.create_task(_minio_save(req_id, arte))
    except Exception:
        # MinIO not configured / import failed — ignore silently.
        pass

    try:
        if not getattr(meta_obj, "resolver_path", None):
            setattr(meta_obj, "resolver_path", "direct")
    except Exception:
        try:
            meta_obj["resolver_path"] = "direct"  # type: ignore[index]
        except Exception:
            pass

    # Strategic: single audit log of pool/prompt/payload cardinalities
    try:
        log_stage("meta", "pool_prompt_payload",
                  request_id=req_id,
                  pool=len(meta_inputs.evidence_sets.pool_ids),
                  prompt=len(meta_inputs.evidence_sets.prompt_included_ids),
                  payload=len(meta_inputs.evidence_sets.payload_included_ids))
    except Exception:
        pass
    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=meta_obj.model_dump(),
        bundle_url=bundle_url,
    )
    arte["response.json"]         = resp.model_dump_json().encode()
    # Duplicate the concise report for the final bundle so downstream consumers
    # receive a lean summary of unknown keys removed.
    _unk_anchor_f: set[str] = set()
    _unk_event_f: set[str] = set()
    _unk_trans_f: set[str] = set()
    for _ve in (errs or []):
        _code = _ve.get("code")
        _det = _ve.get("details") or {}
        _rem = _det.get("removed_keys") or []
        if _code == "unknown_anchor_keys_stripped":
            _unk_anchor_f.update(_rem)
        elif _code == "unknown_event_keys_stripped":
            _unk_event_f.update(_rem)
        elif _code == "unknown_transition_keys_stripped":
            _unk_trans_f.update(_rem)
    _validator_report_f: Dict[str, Any] = {}
    if _unk_event_f:
        _validator_report_f["unknown_event_keys_removed"] = sorted(_unk_event_f)
    if _unk_trans_f:
        _validator_report_f["unknown_transition_keys_removed"] = sorted(_unk_trans_f)
    if _unk_anchor_f:
        _validator_report_f["unknown_anchor_keys_removed"] = sorted(_unk_anchor_f)
    arte["validator_report.json"] = jsonx.dumps(_validator_report_f).encode("utf-8")
    # Persist canonical meta as a sidecar artefact for the audit drawer
    try:
        arte["_meta.json"] = jsonx.dumps(meta_dict).encode("utf-8")
    except Exception:
        pass

    # Return response with enriched meta (dict)
    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=meta_dict,
        bundle_url=bundle_url,
    )
    # Keep response.json aligned with enriched meta
    arte["response.json"] = resp.model_dump_json().encode()
    return resp, arte, req_id
