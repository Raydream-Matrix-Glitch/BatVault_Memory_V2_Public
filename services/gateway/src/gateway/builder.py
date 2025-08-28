from __future__ import annotations
import time, uuid, os, re
from typing import Any, Mapping, Tuple, Dict, List
from core_logging import log_stage

import orjson

import importlib.metadata as _md
from core_logging import log_stage, get_logger
from core_utils.fingerprints import canonical_json
from core_models.models import (
    WhyDecisionAnchor, WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse, WhyDecisionTransitions, CompletenessFlags,
)

from gateway.budget_gate import run_gate
from gateway.llm_router import last_call as _llm_last_call
from .prompt_envelope import build_prompt_envelope
from .templater import deterministic_short_answer
from .templater import finalise_short_answer
from core_validator import validate_response as _core_validate_response
# Import the public canonical helper from core_validator.  This avoids
# depending on a private underscore‑prefixed function which may change in
# future releases.
from core_validator import canonical_allowed_ids
from . import llm_client
import gateway.templater as templater
import inspect


logger   = get_logger("gateway.builder")

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
        # Build the evidence from the Memory‑API given an anchor ID.  The
        # EvidenceBuilder contract should normally never return ``None``.
        # However, tests may monkey‑patch the builder to return ``None`` or
        # the builder could fail open if upstream dependencies are down.
        # In those cases we degrade gracefully by constructing a minimal
        # evidence stub.  This behaviour ensures the Gateway never throws
        # an ``AttributeError`` when accessing ``model_dump`` on a ``None``
        # object and aligns with the tech‑spec requirement that unknown
        # decisions still produce a valid bundle (spec §B2/B5).
        # Robustly call the builder even if tests monkey‑patch the instance-level
        # build method.  If an unbound override exists in the instance __dict__
        # call it directly to avoid double-binding.
        maybe = evidence_builder.build(req.anchor_id)
        ev = await maybe if inspect.isawaitable(maybe) else maybe
        if ev is None:  # pragma: no cover – defensive fallback
            # Fallback: produce an empty evidence bundle with the given
            # anchor ID.  This stub has no events or transitions and a
            # conservative snapshot etag.  ``allowed_ids`` will be
            # recomputed below, so leave it empty here.
            ev = WhyDecisionEvidence(
                anchor=WhyDecisionAnchor(id=req.anchor_id),
                events=[],
                transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
            )
            # ``snapshot_etag`` is an optional field excluded from the
            # default model dump.  Set it explicitly so downstream
            # fingerprinting and caching behave deterministically.
            ev.snapshot_etag = "unknown"
    else:                       # safeguard – should be caught by AskIn validator
        ev = WhyDecisionEvidence(
            anchor=WhyDecisionAnchor(id="unknown"),
            events=[],
            transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
        )

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
        log_stage(logger, "builder", "allowed_ids_canonicalization_failed",
                  error=str(e), request_id=getattr(req, "request_id", None))
        raise
    # Persist the final evidence with empty collections omitted
    arte["evidence_final.json"] = orjson.dumps(ev.model_dump(mode="python", exclude_none=True))
    try:
        log_stage(logger, "builder", "evidence_final_persisted",
                  request_id=req_id,
                  allowed_ids=len(getattr(ev, "allowed_ids", []) or []))
    except Exception:
        pass

    # Build a pre-envelope (the gate will strip evidence when computing overhead)
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
        log_stage(logger, "builder", "allowed_ids_recanonicalize_failed",
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
            log_stage(logger, "prompt", "llm_gate", llm_mode=settings.llm_mode, use_llm=use_llm)

        except Exception:
            pass
        # Preflight health check: if the configured control endpoint is unreachable,
        # disable LLM for this request to avoid noisy “fallback_used=true” semantics.
        if use_llm:
            import httpx, os as _os
            # lazily import OTEL header injector and propagate context to the health check
            try:
                from core_observability.otel import inject_trace_context  # type: ignore
            except Exception:
                inject_trace_context = None  # type: ignore
            ep = (_os.getenv("CONTROL_MODEL_ENDPOINT") or "").rstrip("/")
            hc = f"{ep}/health" if ep else ""
            unhealthy = False
            try:
                if not ep:
                    unhealthy = True
                else:
                    hdrs = inject_trace_context({}) if inject_trace_context else {}
                    resp = httpx.get(hc, timeout=0.3, headers=hdrs)
                    unhealthy = resp.status_code >= 500
            except Exception:
                unhealthy = True
            if unhealthy:
                use_llm = False
                try:
                    log_stage(
                        logger, "llm", "unhealthy",
                        request_id=req_id, endpoint=ep or "unset",
                        reason="healthcheck_failed", llm_mode=settings.llm_mode
                    )
                except Exception:
                    pass
        # If the LLM is explicitly disabled by config, record that clearly and mark fallback.
        if not use_llm:
            try:
                log_stage(
                    logger, "llm", "disabled",
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
                # Perform a single summarisation call; summarise_json will
                # internally call the llm_router and apply retries.  When
                # the LLM is unavailable, summarise_json returns a deterministic
                # stub that we treat as a fallback.  Use the request_id for
                # stable canary routing.
                raw_json = llm_client.summarise_json(
                    envelope,
                    temperature=temp,
                    max_tokens=max_tokens,
                    retries=max_retries,
                    request_id=req_id,
                    messages_override=gate_plan.messages,
                    max_tokens_override=gate_plan.max_tokens,
                )
            except Exception:
                raw_json = None
            # No explicit retry loop here; summarise_json handles its own retries.
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
                    log_stage(
                        logger, "llm", "fallback",
                        request_id=req_id,
                        reason="no_raw_json",
                        openai_disabled=os.getenv("OPENAI_DISABLED"),
                        canary_pct=os.getenv("CANARY_PCT"),
                        control=os.getenv("CONTROL_MODEL_ENDPOINT"),
                        canary=os.getenv("CANARY_MODEL_ENDPOINT"),
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
            support: list[str] = []
            try:
                if ev.anchor and getattr(ev.anchor, "id", None):
                    support.append(ev.anchor.id)
                evs = [e for e in (ev.events or []) if isinstance(e, dict)]
                evs_sorted = sorted(evs, key=lambda e: e.get("timestamp") or "")[-3:]
                support += [e.get("id") for e in evs_sorted if e.get("id")]
                for t in (ev.transitions.preceding or []) + (ev.transitions.succeeding or []):
                    tid = t.get("id") if isinstance(t, dict) else getattr(t, "id", None)
                    if tid:
                        support.append(tid)
                if not support and (ev.allowed_ids or []):
                    support = list(ev.allowed_ids)
                seen: set[str] = set()
                support = [x for x in support if x and not (x in seen or seen.add(x))]
            except Exception:
                support = [ev.anchor.id] if (ev.anchor and getattr(ev.anchor, "id", None)) else []

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
                        log_stage(
                            logger, "llm", "fallback",
                            request_id=req_id,
                            reason="stub_answer",
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
                    log_stage(
                        logger, "llm", "fallback",
                        request_id=req_id,
                        reason="parse_error", detail=str(e)[:200],
                    )
                except Exception:
                    pass
                # Build richer supporting_ids from evidence (anchor + events + transitions),
                # keeping ordering compatible with allowed_ids and deduping.
                support: list[str] = []
                try:
                    if ev.anchor and getattr(ev.anchor, "id", None):
                        support.append(ev.anchor.id)
                    # include up to 3 most recent events
                    evs = [e for e in (ev.events or []) if isinstance(e, dict)]
                    evs_sorted = sorted(evs, key=lambda e: e.get("timestamp") or "")[-3:]
                    support += [e.get("id") for e in evs_sorted if e.get("id")]
                    # include all present transitions (preceding + succeeding)
                    for t in (ev.transitions.preceding or []) + (ev.transitions.succeeding or []):
                        tid = t.get("id") if isinstance(t, dict) else getattr(t, "id", None)
                        if tid:
                            support.append(tid)
                    # fall back to allowed_ids if the bundle is small and anchor wasn’t set
                    if not support and (ev.allowed_ids or []):
                        support = list(ev.allowed_ids)
                    # dedupe preserving order
                    seen: set[str] = set()
                    support = [x for x in support if x and not (x in seen or seen.add(x))]
                except Exception:
                    support = [ev.anchor.id] if (ev.anchor and getattr(ev.anchor, "id", None)) else []

                ans = WhyDecisionAnswer(short_answer="", supporting_ids=support)
                arte["llm_raw.json"] = b"{}"

    # ── LLM prose clamp ---------------------------------------------------
    # Post-process the LLM's short_answer to remove raw IDs and enforce the
    # concise style.  If any allowed_id appears as a standalone token in
    # the short_answer it is removed and the change is recorded via a
    # structured log.  After scrubbing, the clamp checks for violations of
    # the style spec: length >320 characters, more than two sentences, or
    # missing maker/date prefix when the evidence anchor carries both
    # decision_maker and timestamp.  If any violation is found a
    # deterministic fallback answer is synthesised and flagged as a
    # style_violation fallback.
    if ans is not None and isinstance(ans.short_answer, str) and not llm_fallback:
        try:
            short = ans.short_answer or ""
            removed_count = 0
            # Remove raw IDs appearing as whole tokens
            for _id in (ev.allowed_ids or []):
                if not _id:
                    continue
                # match id as a whole word (boundaries by word chars)
                pattern = rf"\b{re.escape(_id)}\b"
                matches = re.findall(pattern, short)
                if matches:
                    removed_count += len(matches)
                    short = re.sub(pattern, "", short)
            # Normalise whitespace after removals
            if removed_count:
                short = re.sub(r"\s+", " ", short).strip()
                try:
                    log_stage(logger, "answer", "scrubbed_ids", request_id=req_id, scrubbed=True, count=removed_count)
                except Exception:
                    pass
            else:
                # log that no ids were scrubbed
                try:
                    log_stage(logger, "answer", "scrubbed_ids", request_id=req_id, scrubbed=False, count=0)
                except Exception:
                    pass
            style_invalid = False
            # Too long (>320 characters)
            if len(short) > 320:
                style_invalid = True
            # Too many sentences: split on ., ! or ?
            segs = [s for s in re.split(r"[.!?]", short) if s.strip()]
            if len(segs) > 2:
                style_invalid = True
            # Ensure maker/date prefix when available
            maker = ""
            date_part = ""
            try:
                maker = (ev.anchor.decision_maker or "").strip() if ev.anchor else ""
                ts = (ev.anchor.timestamp or "").strip() if ev.anchor else ""
                date_part = ts.split("T")[0] if ts else ""
            except Exception:
                maker = ""; date_part = ""
            if maker and date_part:
                prefix = f"{maker} on {date_part}"
                if not short.startswith(prefix):
                    style_invalid = True
            # If invalid style, synthesise deterministic fallback
            if style_invalid:
                llm_fallback = True
                fallback_reason = "style_violation"
                try:
                    short = templater._compose_fallback_answer(ev)
                except Exception:
                    short = templater.deterministic_short_answer(ev)
                ans.short_answer = short
            else:
                # enforce the scrubbed/trimmed short answer back on ans
                ans.short_answer = short
        except Exception:
            # Defensive: if clamping fails use fallback
            llm_fallback = True
            fallback_reason = "style_violation"
            try:
                ans.short_answer = templater._compose_fallback_answer(ev)
            except Exception:
                ans.short_answer = templater.deterministic_short_answer(ev)

    # ── adjust supporting_ids using templater (legacy) ───────────────
    # The Gateway previously invoked ``templater.validate_and_fix`` to
    # perform legacy supporting_id repairs.  This logic is now entirely
    # handled by the core validator.  We retain the variables for
    # compatibility but do not invoke the templater.
    changed_support = False
    templater_errs: list[str] = []

    # Proactively ensure supporting_ids are a subset of allowed_ids and
    # deduplicated whilst preserving relative order.  This clamps any
    # stray identifiers introduced by the LLM prior to validation.  The
    # core validator performs the definitive enforcement, but applying
    # this here ensures consumers never see unsupported IDs in the
    # response even if the validator is relaxed.
    try:
        if ans is not None and isinstance(ans.supporting_ids, list) and (ev.allowed_ids or []):
            cleaned: List[str] = []
            seen_ids: set[str] = set()
            for sid in ans.supporting_ids:
                if sid in ev.allowed_ids and sid not in seen_ids:
                    cleaned.append(sid)
                    seen_ids.add(sid)
            # Always include the anchor ID first
            anchor_id = getattr(ev.anchor, "id", None)
            if anchor_id and anchor_id in ev.allowed_ids and anchor_id not in seen_ids:
                cleaned.insert(0, anchor_id)
            ans.supporting_ids = cleaned
    except Exception:
        pass

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
            from . import prom_metrics as _pm
            _pm.gateway_llm_fallback_total.inc()
        except Exception:
            pass
        log_stage(get_logger("builder"), "builder", "llm_fallback_used",
                  request_id=req_id,
                  prompt_fp=prompt_fp,
                  bundle_fp=bundle_fp)
    if sel_meta.get("selector_truncation"):
        log_stage(get_logger("builder"), "builder", "selector_truncated",
                  request_id=req_id,
                  prompt_tokens=sel_meta.get("prompt_tokens"),
                  max_prompt_tokens=sel_meta.get("max_prompt_tokens"))
    # Ensure `meta` exists for the pre-validation response below. It will be
    # fully populated after validation (see later `meta = { ... }` assignment).
    meta: Dict[str, Any] = {}

    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta={
            **meta,
            "request_id": req_id,
            "llm": {
                "model": llm_model,
                "canary": bool(llm_canary) if llm_canary is not None else None,
                "attempts": llm_attempt,
                "latency_ms": llm_latency,
            },
        },
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

    # Combine all error messages.  Structured errors originate from the core
    # validator.  Legacy templater string errors are no longer appended.
    errs: list = []
    if validator_errs:
        errs.extend(validator_errs)
    if errs:
        log_stage(get_logger("builder"), "builder", "validator_repaired",
                  request_id=req_id, error_count=len(errs))

    # ── persist artefacts ───────────────────────────────────────────
    arte["envelope.json"] = orjson.dumps(envelope)
    arte["rendered_prompt.txt"] = canonical_json(envelope)
    arte.setdefault("llm_raw.json", b"{}")

    # Determine gateway version with environment override
    import os as _os
    gw_version = _os.getenv("GATEWAY_VERSION", _GATEWAY_VERSION)

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
    fallback_used = bool(llm_fallback or fatal_validation)
    # Log an explicit fallback reason for traceability
    try:
        if llm_fallback:
            log_stage(get_logger("builder"), "builder", "llm_fallback_used",
                      request_id=req_id, prompt_fp=prompt_fp, bundle_fp=bundle_fp)
        elif fallback_used:
            # validator set fallback (fatal)
            codes = [e.get("code") for e in validator_errs or [] if e.get("code") in fatal_codes]
            log_stage(get_logger("builder"), "builder", "validator_fallback",
                      request_id=req_id, codes=codes)
        else:
            # no fallback – but we still log if non-fatal repairs happened for observability
            if validator_errs:
                non_fatal = [e.get("code") for e in validator_errs if e.get("code") not in fatal_codes]
                if non_fatal:
                    log_stage(get_logger("builder"), "builder", "validator_repaired_nonfatal",
                              request_id=req_id, codes=non_fatal)
    except Exception:
        pass

    _policy = envelope.get("policy") or {}
    _policy_id = _policy.get("policy_id") or envelope.get("policy_id") or "unknown"
    _prompt_id = envelope.get("prompt_id") or "unknown"
    fallback_reason_clean = fallback_reason if fallback_reason is not None else None

    meta = {
        "policy_id": _policy_id,
        "prompt_id": _prompt_id,
        "prompt_fingerprint": prompt_fp,
        "bundle_fingerprint": bundle_fp,
        # legacy compat
        "bundle_size_bytes": len(orjson.dumps(ev.model_dump(mode="python"))),
        # new token-aware fields for audit
        "prompt_tokens": int(gate_plan.prompt_tokens),
        "snapshot_etag": snapshot_etag_fp,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason_clean,
        "retries": int(retry_count),
        "gateway_version": gw_version,
        "selector_model_id": SELECTOR_MODEL_ID,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "validator_errors": errs,
        "evidence_metrics": sel_meta,
        "load_shed": should_load_shed(),
    }

    try:
        ev_etag = getattr(ev, "snapshot_etag", None) or meta.get("snapshot_etag") or "unknown"
        anchor_id = getattr(ev.anchor, "id", None) or "unknown"
        log_stage(logger, "builder", "etag_propagated",
                  anchor_id=anchor_id, snapshot_etag=ev_etag)
    except Exception:
        pass

    # Add a helpful footnote when we fall back / retry
    try:
        # Attach a rationale note when a fallback path was taken (LLM fallback or repairs)
        if meta.get("fallback_used") and not getattr(ans, "rationale_note", None):
            ans.rationale_note = "Templater fallback (LLM unavailable/failed)."
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
        meta=meta,
        bundle_url=bundle_url,
    )
    arte["response.json"]         = resp.model_dump_json().encode()
    arte["validator_report.json"] = orjson.dumps({"errors": errs})
    return resp, arte, req_id
