from __future__ import annotations
import time, uuid
from typing import Any, Mapping, Tuple, Dict
from core_logging import log_stage

import orjson

import importlib.metadata as _md
from core_logging import get_logger
from core_utils.fingerprints import canonical_json
from core_models.models import (
    WhyDecisionAnchor, WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse, WhyDecisionTransitions, CompletenessFlags,
)

from .selector import truncate_evidence, bundle_size_bytes
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

try:
    _GATEWAY_VERSION = _md.version("batvault_gateway")
except _md.PackageNotFoundError:
    _GATEWAY_VERSION = "unknown"

from core_config.constants import SELECTOR_MODEL_ID


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

    arte["evidence_pre.json"] = orjson.dumps(ev.model_dump(mode="python"))

    # ── selector: truncate if > MAX_PROMPT_BYTES ───────────────
    ev, sel_meta = truncate_evidence(ev)
    arte["evidence_post.json"] = orjson.dumps(ev.model_dump(mode="python"))

    # ── deterministic plan stub (needed for audit contract) ────────────
    plan_dict = {"node_id": ev.anchor.id, "k": 1}
    arte["plan.json"] = orjson.dumps(plan_dict)

    # Compute canonical allowed_ids using the core validator helper.  This
    # ensures that the anchor appears first, events follow in ascending
    # timestamp order and transitions follow thereafter.  Duplicate IDs are
    # removed.  The evidence may contain typed model instances; convert
    # these to plain dictionaries for the canonical helper.
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
        # Surface the problem loudly; canonical IDs are part of the contract.
        log_stage(logger, "builder", "allowed_ids_canonicalization_failed",
                  error=str(e), request_id=getattr(req, "request_id", None))
        raise

    # ── canonical prompt envelope + fingerprint ────────────────
    envelope = build_prompt_envelope(
        question=f"Why was decision {ev.anchor.id} made?",
        evidence=ev.model_dump(mode="python"),
        snapshot_etag=getattr(ev, "snapshot_etag", "unknown"),
        intent=req.intent,
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )

    # ── answer generation with JSON‑only LLM and deterministic fallback ──
    raw_json: str | None = None
    llm_fallback = False
    retry_count = 0
    ans: WhyDecisionAnswer | None = None

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
            max_tokens = int(envelope.get("constraints", {}).get("max_tokens", 256))
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
                )
            except Exception:
                raw_json = None
            # No explicit retry loop here; summarise_json handles its own retries.
            retry_count = max_retries
        # Determine whether this is a fallback: if we didn't call the LLM
        # (use_llm is false) or summarise_json returned no result.
        if raw_json is None:
            # The LLM did not run or returned no result.  Flag this as a fallback if
            # use_llm was true.  Do not synthesise the short_answer here; leave
            # short_answer empty so finalise_short_answer can compute a fallback
            # based on the evidence.  We still set supporting_ids based on the
            # anchor or allowed_ids to satisfy the contract.
            llm_fallback = use_llm
            supp_id: str | None = None
            try:
                if ev.anchor and ev.anchor.id and ev.anchor.id in ev.allowed_ids:
                    supp_id = ev.anchor.id
                elif ev.allowed_ids:
                    supp_id = ev.allowed_ids[0]
            except Exception:
                # If both anchor and allowed_ids are missing, leave supporting_ids empty
                supp_id = ev.anchor.id if getattr(ev.anchor, "id", None) else (ev.allowed_ids[0] if ev.allowed_ids else None)
            ans = WhyDecisionAnswer(
                short_answer="",
                supporting_ids=[supp_id] if supp_id else [],
            )
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
            except Exception:
                # Parsing or validation failed – treat as a fallback.  Leave the
                # short answer empty so the templater can synthesise a deterministic
                # fallback.  Populate supporting_ids based on the anchor or
                # allowed_ids.
                llm_fallback = True
                supp_id: str | None = None
                try:
                    if ev.anchor and ev.anchor.id and ev.anchor.id in ev.allowed_ids:
                        supp_id = ev.anchor.id
                    elif ev.allowed_ids:
                        supp_id = ev.allowed_ids[0]
                except Exception:
                    supp_id = ev.anchor.id if getattr(ev.anchor, "id", None) else (ev.allowed_ids[0] if ev.allowed_ids else None)
                ans = WhyDecisionAnswer(
                    short_answer="",
                    supporting_ids=[supp_id] if supp_id else [],
                )
                arte["llm_raw.json"] = b"{}"

    # ── adjust supporting_ids using templater (legacy) ───────────────
    # The Gateway previously invoked ``templater.validate_and_fix`` to
    # perform legacy supporting_id repairs.  This logic is now entirely
    # handled by the core validator.  We retain the variables for
    # compatibility but do not invoke the templater.
    changed_support = False
    templater_errs: list[str] = []

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
    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta={},
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

    # ── persist artefacts ───────────────────────────────────────────
    arte["envelope.json"] = orjson.dumps(envelope)
    arte["rendered_prompt.txt"] = canonical_json(envelope)
    arte.setdefault("llm_raw.json", b"{}")

    # Determine gateway version with environment override
    import os as _os
    gw_version = _os.getenv("GATEWAY_VERSION", _GATEWAY_VERSION)

    # Determine fallback_used flag: true if LLM was unavailable or any repairs were made
    fallback_used = bool(
        llm_fallback or validator_errs or finalise_changed
    )

    meta = {
        "policy_id": envelope["policy_id"],
        "prompt_id": envelope["prompt_id"],
        "prompt_fingerprint": envelope["_fingerprints"]["prompt_fingerprint"],
        "bundle_fingerprint": envelope["_fingerprints"]["bundle_fingerprint"],
        "bundle_size_bytes": bundle_size_bytes(ev),
        "snapshot_etag": envelope["_fingerprints"]["snapshot_etag"],
        "fallback_used": fallback_used,
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

    resp = WhyDecisionResponse(
        intent=req.intent,
        evidence=ev,
        answer=ans,
        completeness_flags=flags,
        meta=meta,
    )
    arte["response.json"]         = resp.model_dump_json().encode()
    arte["validator_report.json"] = orjson.dumps({"errors": errs})
    return resp, arte, req_id
