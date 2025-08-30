from __future__ import annotations
import time, uuid, os, re
from typing import Any, Mapping, Tuple, Dict, List

import orjson
from core_config import get_settings

import importlib.metadata as _md
from core_logging import get_logger
from .logging_helpers import stage as log_stage
from core_utils.fingerprints import canonical_json
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

from gateway.budget_gate import run_gate
from shared.normalize import normalise_event_amount as _normalise_event_amount  # promoted helper
from gateway import selector as _selector
from gateway.inference_router import last_call as _llm_last_call
from .prompt_envelope import build_prompt_envelope
from .templater import deterministic_short_answer
from .templater import finalise_short_answer
from core_validator import validate_response as _core_validate_response
# Import the public canonical helper from core_validator.  This avoids
# depending on a private underscore‑prefixed function which may change in
# future releases.
from core_validator import canonical_allowed_ids
from gateway.inference_router import call_llm as llm_call
import gateway.templater as templater
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_supporting_ids(ev: WhyDecisionEvidence) -> list[str]:
    """Compute deterministic supporting_ids for the given evidence.

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
    first_suc_id: str | None = None
    try:
        suc_list = list(getattr(ev.transitions, "succeeding", []) or [])
    except Exception:
        suc_list = []
    for tr in suc_list:
        tid = tr.get("id") if isinstance(tr, dict) else getattr(tr, "id", None)
        if tid:
            first_suc_id = tid
            break
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
    try:
        events = list(ev.events or [])
    except Exception:
        return
    # Step 1: deduplicate by event ID
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for item in events:
        try:
            eid = item.get("id")
        except Exception:
            eid = None
        if eid and eid in seen_ids:
            continue
        if eid:
            seen_ids.add(eid)
        deduped.append(item)
    events = deduped
    # Step 2: deduplicate near‑identical events on the same day
    try:
        import re as _re
        grouped: set[tuple] = set()
        uniq: list[dict] = []
        for item in events:
            try:
                ts = item.get("timestamp") or ""
                date_part = ts.split("T")[0] if isinstance(ts, str) else str(ts)[:10]
                summary = item.get("summary") or item.get("description") or ""
                if not isinstance(summary, str):
                    summary = str(summary)
                # Remove digits, currency symbols and punctuation; collapse whitespace
                key_text = re.sub(r"[\d$¥€£,\.\s]+", "", summary).lower()
                dedup_key = (date_part, key_text)
            except Exception:
                dedup_key = (item.get("timestamp"), item.get("id"))
            if dedup_key in grouped:
                continue
            grouped.add(dedup_key)
            uniq.append(item)
        events = uniq
    except Exception:
        pass
    # Step 3: normalise monetary amounts
    for item in events:
        try:
            _normalise_event_amount(item)
        except Exception:
            pass
    try:
        ev.events = events
    except Exception:
        pass

# In‑memory cache for artefact bundles.  Each entry stores the exact
# artefacts used to assemble a response keyed by the request ID.  The
# ``/v2/bundles/{request_id}`` endpoint reads from this cache when
# streaming a bundle back to the caller.  See ``gateway.app`` for
# download routes.  Keeping the cache here avoids a circular import.
BUNDLE_CACHE: Dict[str, Dict[str, bytes]] = {}

try:
    _GATEWAY_VERSION = _md.version("batvault_gateway")
except _md.PackageNotFoundError:
    _GATEWAY_VERSION = "unknown"

from core_config.constants import SELECTOR_MODEL_ID
from .load_shed import should_load_shed


# ───────────────────── main entry-point ─────────────────────────
async def build_why_decision_response(
    req: "AskIn",                          # forward-declared (defined in app.py)
    evidence_builder,                      # EvidenceBuilder instance (singleton passed from app.py)
) -> Tuple[WhyDecisionResponse, Dict[str, bytes], str]:
    """
    Assemble Why-Decision response and audit artefacts.
    Returns (response, artefacts_dict, request_id).
    """
    t0      = time.perf_counter()
    req_id  = req.request_id or uuid.uuid4().hex
    arte: Dict[str, bytes] = {}

    # ── evidence (k = 1 collect) ───────────────────────────────
    ev: WhyDecisionEvidence
    if req.evidence is not None:
        ev = req.evidence
    elif req.anchor_id:
        maybe = evidence_builder.build(req.anchor_id)
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
    arte["evidence_pre.json"] = orjson.dumps(ev.model_dump(mode="python", exclude_none=True))

    # ── deterministic plan stub (needed for audit contract) ────────────
    plan_dict = {"node_id": ev.anchor.id, "k": 1}
    arte["plan.json"] = orjson.dumps(plan_dict)

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
        ev.allowed_ids = canonical_allowed_ids(
            getattr(ev.anchor, "id", None) or "",
            ev_events,
            ev_trans,
        )
    except Exception as e:
        log_stage("builder", "allowed_ids_canonicalization_failed",
                  error=str(e), request_id=getattr(req, "request_id", None))
        raise
    # Persist the final evidence with empty collections omitted
    arte["evidence_final.json"] = orjson.dumps(ev.model_dump(mode="python", exclude_none=True))
    try:
        log_stage("builder", "evidence_final_persisted",
                  request_id=req_id,
                  allowed_ids=len(getattr(ev, "allowed_ids", []) or []))
    except Exception:
        pass

    # Build a pre-envelope (the gate will strip evidence when computing overhead)
    # Warm the policy registry via unified schema cache (P1: collapse schema fetching)
    try:
        from .schema_cache import fetch_schema as _fetch_schema
        await _fetch_schema("/api/policy/registry")
        try:
            log_stage("schema", "policy_registry_warmed", request_id=req_id)
        except Exception:
            pass
    except Exception:
        # Non-fatal: fall back to local file in prompt_envelope loader
        try:
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
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )
    from gateway.budget_gate import run_gate as _run_gate
    gate_plan, trimmed_evidence = _run_gate(pre_envelope, ev, request_id=req_id, model_name=None)
    # Persist trimmed evidence & re-canonicalise allowed_ids to drop removed items
    try:
        ev = trimmed_evidence if isinstance(trimmed_evidence, WhyDecisionEvidence) \
             else WhyDecisionEvidence.model_validate(trimmed_evidence)
        ev_events = []
        for e in (ev.events or []):
            ev_events.append(e if isinstance(e, dict) else getattr(e, "model_dump", dict)(mode="python"))
        ev_trans = []
        for t in list(getattr(ev.transitions, "preceding", []) or []) + list(getattr(ev.transitions, "succeeding", []) or []):
            ev_trans.append(t if isinstance(t, dict) else getattr(t, "model_dump", dict)(mode="python"))
        ev.allowed_ids = canonical_allowed_ids(
            getattr(ev.anchor, "id", None) or "",
            ev_events,
            ev_trans,
        )
    except Exception as e:
        log_stage("builder", "allowed_ids_recanonicalize_failed",
                  error=str(e), request_id=req_id)
    # Normalise empty transition lists to None before serialising the post-gate evidence.
    # When a field is None Pydantic can omit it from the JSON (exclude_none=True).
    try:
        if not (getattr(ev.transitions, "preceding", []) or []):
            ev.transitions.preceding = None  # type: ignore
        if not (getattr(ev.transitions, "succeeding", []) or []):
            ev.transitions.succeeding = None  # type: ignore
    except Exception:
        pass
    arte["evidence_post.json"] = orjson.dumps(ev.model_dump(mode="python", exclude_none=True))
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
    # Keep the response events to top 10 (bundle artefacts already captured above retain the full list)
    ev.events = _ranked_all[:10]
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
        evidence=ev.model_dump(mode="python", exclude_none=True),
        snapshot_etag=getattr(ev, "snapshot_etag", "unknown"),
        intent=req.intent,
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )
    # Fingerprints from the gate (single source of truth for prompt)
    prompt_fp: str = (gate_plan.fingerprints or {}).get("prompt") or "unknown"
    bundle_fp: str = envelope.get("_fingerprints", {}).get("bundle_fingerprint") or "unknown"
    snapshot_etag_fp: str = envelope.get("_fingerprints", {}).get("snapshot_etag") or "unknown"

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
        from core_config import get_settings
        settings = get_settings()
        # Spec: “LLM does one thing only … and only when llm_mode != off”
        # Values: off|on|auto (treat auto as on here; routing still handles load-shed)
        use_llm = (settings.llm_mode or "off").lower() != "off"
        # Strategic log (B5 envelope): makes the gate visible in traces & audit
        try:
            log_stage("prompt", "llm_gate", llm_mode=settings.llm_mode, use_llm=use_llm)

        except Exception:
            pass
        # If the LLM is explicitly disabled by config, record that clearly and mark fallback.
        if not use_llm:
            try:
                log_stage("llm", "disabled",
                    request_id=req_id,
                    llm_mode=settings.llm_mode,
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
            except Exception:
                # On any exception treat as no raw JSON (fallback path)
                raw_json = None
            # No explicit retry loop here; llm_call delegates retries.
            retry_count = max_retries
        # Determine whether this is a fallback: if we didn't call the LLM
        # (use_llm is false) or summarise_json returned no result.
        if raw_json is None:
            # The LLM did not run or returned no result.  Always mark this as a fallback
            # because a deterministic answer must be synthesised.  Preserve any
            # previously set fallback_reason (e.g. "llm_off") and use
            # "no_raw_json" when the LLM was expected to run.
            llm_fallback = True
            if use_llm:
                # Strategic log: immediate fallback decision (+ environment for audit)
                try:
                    log_stage("llm", "fallback_decision",
                        request_id=req_id,
                        reason="no_raw_json",
                        openai_disabled=getattr(s, "openai_disabled", False),
                        canary_pct=getattr(s, "canary_pct", 0),
                        control=getattr(s, "control_model_endpoint", ""),
                        canary=getattr(s, "canary_model_endpoint", ""),
                    )
                except Exception:
                    pass
                if not fallback_reason:
                    fallback_reason = "no_raw_json"
            else:
                # No LLM expected; if no explicit reason already set assign llm_off
                if not fallback_reason:
                    fallback_reason = "llm_off"
            # Same richer supporting_ids logic for the “no raw_json” branch
            # Compute supporting ids deterministically
            try:
                support = _compute_supporting_ids(ev)
            except Exception:
                support = []
                try:
                    aid = getattr(ev.anchor, "id", None)
                    if aid:
                        support.append(aid)
                except Exception:
                    support = []
            ans = WhyDecisionAnswer(short_answer="", supporting_ids=support)
            arte["llm_raw.json"] = b"{}"
        else:
            arte["llm_raw.json"] = raw_json.encode()
            try:
                parsed = orjson.loads(raw_json)
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
                # fallback.  Populate supporting_ids based on the anchor or
                # allowed_ids.
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
                # Build richer supporting_ids from evidence (anchor + events + transitions),
                # keeping ordering compatible with allowed_ids and deduping.
                try:
                    support = _compute_supporting_ids(ev)
                except Exception:
                    support = []
                    try:
                        aid = getattr(ev.anchor, "id", None)
                        if aid:
                            support.append(aid)
                    except Exception:
                        support = []

                ans = WhyDecisionAnswer(short_answer="", supporting_ids=support)
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
        llm_model   = _llm_last_call.get("model")
        llm_canary  = _llm_last_call.get("canary")
        llm_attempt = _llm_last_call.get("attempt")
        llm_latency = _llm_last_call.get("latency_ms")
    except Exception:
        pass

    # --- Strategic logging on pivotal decisions ------------------------------
    if llm_fallback:
        try:
            from .metrics import counter as _metric_counter
            _metric_counter('gateway_llm_fallback_total', 1)
        except Exception:
            pass
        log_stage("builder", "llm_fallback_used",
                  request_id=req_id,
                  prompt_fp=prompt_fp,
                  bundle_fp=bundle_fp)
    if sel_meta.get("selector_truncation"):
        log_stage("builder", "selector_truncated",
                  request_id=req_id,
                  prompt_tokens=sel_meta.get("prompt_tokens"),
                  max_prompt_tokens=sel_meta.get("max_prompt_tokens"))
    # Ensure `meta` exists for the pre-validation response below. It will be
    # fully populated after validation (see later `meta = { ... }` assignment).
    # Build a **complete** MetaInfo for pre-validation response to avoid schema errors.
    # Even though some fields (e.g. validator_error_count, fallback_used) are provisional
    # at this point, the contract requires them to be present. They will be finalised later.
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

    _meta_pre = MetaInfo(
        policy_id=_policy_id_pre,
        prompt_id=_prompt_id_pre,
        prompt_fingerprint=prompt_fp,
        bundle_fingerprint=bundle_fp,
        bundle_size_bytes=int(len(arte.get("evidence_post.json", b""))),
        prompt_tokens=int(getattr(gate_plan, "prompt_tokens", 0)),
        max_tokens=int(getattr(gate_plan, "max_tokens", 0)),
        evidence_tokens=int(getattr(gate_plan, "evidence_tokens", 0)),
        snapshot_etag=snapshot_etag_fp,
        fallback_used=bool(llm_fallback),
        fallback_reason=fallback_reason if llm_fallback else None,
        retries=int(retry_count),
        gateway_version=_gw_version_pre,
        selector_model_id=SELECTOR_MODEL_ID,
        latency_ms=int((time.perf_counter() - t0) * 1000.0),
        validator_error_count=0,
        evidence_metrics=dict(_sel_metrics_pre) if _sel_metrics_pre else {},
    )
    try:
        log_stage("builder", "meta_prepared_pre_validation",
                  request_id=req_id, policy_id=_policy_id_pre, prompt_id=_prompt_id_pre,
                  snapshot_etag=snapshot_etag_fp)
    except Exception:
        pass

    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=_meta_pre,
    )
    # Validate and normalise the response using the core validator
    # Invoke the validator via the module's global namespace to honour monkey‑patching of
    # ``gateway.builder.validate_response`` in tests.  When tests set
    # gateway.builder.validate_response = <stub>, this call will resolve to the
    # patched function.  Fallback to the core implementation if not found.
    _validator_func = globals().get("validate_response", _core_validate_response)  # type: ignore[name-defined]
    ok, validator_errs = _validator_func(resp)
    # Post-process the short answer to replace stubs and enforce length
    ans, finalise_changed = finalise_short_answer(resp.answer, resp.evidence)
    # Recompute supporting_ids after finalising answer
    try:
        if ans is not None:
            ans.supporting_ids = _compute_supporting_ids(ev)
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
    arte["envelope.json"] = orjson.dumps(envelope)
    arte["rendered_prompt.txt"] = canonical_json(envelope)
    arte.setdefault("llm_raw.json", b"{}")

    # Determine gateway version with environment override
    import os as _os
    gw_version = os.getenv("GATEWAY_VERSION", _GATEWAY_VERSION)

    # Determine fallback_used: true if templater/stub used OR **fatal** validation issues
    # Fatal per spec: JSON parse/schema failure, supporting_ids ⊄ allowed_ids, missing mandatory IDs
    fatal_codes = {
        "LLM_JSON_INVALID",
        "schema_error",
        "supporting_ids_not_subset",
        "supporting_ids_missing_transition",
        "anchor_missing_in_supporting_ids",
    }
    fatal_validation = any((e.get("code") in fatal_codes) for e in (validator_errs or []))
    any_validation   = bool(validator_errs)
    fallback_used = bool(llm_fallback or any_validation)
    # Log an explicit fallback reason for traceability
    try:
        if llm_fallback:
            log_stage("builder", "llm_fallback_used",
                      request_id=req_id, prompt_fp=prompt_fp, bundle_fp=bundle_fp,
                      snapshot_etag=snapshot_etag_fp)
        elif any_validation:
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

    # Assemble canonical meta via the shared builder.  Wrap raw values into
    # MetaInputs to forbid unexpected keys and normalise the fingerprint prefix.
    meta_inputs = MetaInputs(
        policy_id=_policy_id,
        prompt_id=_prompt_id,
        prompt_fingerprint=prompt_fp,
        bundle_fingerprint=bundle_fp,
        bundle_size_bytes=int(len(arte.get("evidence_post.json", b""))),
        prompt_tokens=int(getattr(gate_plan, "prompt_tokens", 0)),
        max_tokens=int(getattr(gate_plan, "max_tokens", 0)),
        evidence_tokens=int(getattr(gate_plan, "evidence_tokens", 0)),
        snapshot_etag=snapshot_etag_fp,
        gateway_version=gw_version,
        selector_model_id=SELECTOR_MODEL_ID,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason_clean,
        retries=int(retry_count),
        latency_ms=int((time.perf_counter() - t0) * 1000),
        validator_error_count=len(errs),
        evidence_metrics=cleaned_metrics,
        load_shed=should_load_shed(),
        events_total=int(_events_total),
        events_truncated=bool(_events_truncated_flag),
    )

    # Build the canonical MetaInfo once; idempotent by design.
    meta_obj = build_meta(meta_inputs, request_id=req_id)

    try:
        # Extract the snapshot etag from the evidence or fallback to the meta value.
        ev_etag = (
            getattr(ev, "snapshot_etag", None)
            or getattr(meta_obj, "snapshot_etag", None)
            or "unknown"
        )
        anchor_id = getattr(ev.anchor, "id", None) or "unknown"
        log_stage(
            logger,
            "builder",
            "etag_propagated",
            anchor_id=anchor_id,
            snapshot_etag=ev_etag,
        )
    except Exception:
        pass

    # Compose a bundle URL for downstream callers.  This is a relative
    # route into the Gateway that returns the JSON artefact bundle for
    # this request.  A POST to ``/v2/bundles/<id>/download`` may be used
    # to obtain a presigned URL in production; here we surface the
    # direct GET endpoint.
    bundle_url = f"/v2/bundles/{req_id}"
    # Persist artefacts into the in-memory bundle cache so the GET
    # endpoint can retrieve the bundle later.  This must be done before
    # returning the response as the app layer imports BUNDLE_CACHE from
    # this module.
    try:
        BUNDLE_CACHE[req_id] = dict(arte)
    except Exception:
        # In the unlikely event we cannot cache the bundle, proceed
        # without raising an error; the GET endpoint will return 404.
        pass

    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=meta_obj,
        bundle_url=bundle_url,
    )
    arte["response.json"]         = resp.model_dump_json().encode()
    arte["validator_report.json"] = orjson.dumps({"errors": errs})
    return resp, arte, req_id
