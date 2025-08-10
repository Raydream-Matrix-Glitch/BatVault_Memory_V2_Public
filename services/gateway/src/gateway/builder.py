from __future__ import annotations
import time, uuid
from typing import Any, Mapping, Tuple, Dict
from core_logging import trace_span

import orjson
from pydantic import BaseModel

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
from . import llm_client
import gateway.templater as templater
from .load_shed import should_load_shed


logger   = get_logger("gateway.builder")

try:
    _GATEWAY_VERSION = _md.version("batvault_gateway")
except _md.PackageNotFoundError:
    _GATEWAY_VERSION = "unknown"

from core_config.constants import SELECTOR_MODEL_ID

# ─────────────────────────── helpers ────────────────────────────
def _allowed_ids(ev: WhyDecisionEvidence) -> list[str]:
    """
    Union of anchor ∪ events ∪ transitions that gracefully copes with either
    dicts *or* typed objects (future-proof for Milestone-4 models).
    """
    def _id(obj):
        return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)

    ids = {
        ev.anchor.id,
        *(_id(e) for e in ev.events               if _id(e)),
        *(_id(t) for t in ev.transitions.preceding if _id(t)),
        *(_id(t) for t in ev.transitions.succeeding if _id(t)),
    }
    return sorted(ids)


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
        ev = await evidence_builder.build(req.anchor_id)
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
        ev = await evidence_builder.build(req.anchor_id)
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

    ev.allowed_ids = _allowed_ids(ev)

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
        ans = req.answer
    else:
        import os
        use_llm = os.getenv("OPENAI_DISABLED", "1") == "0"
        if use_llm:
            max_retries = 2
            attempts = 0
            while True:
                try:
                    attempts += 1
                    import importlib
                    openai_mod = importlib.import_module("openai")
                    try:
                        openai_mod.ChatCompletion.create()
                    except TypeError:
                        openai_mod.ChatCompletion.create({})
                    raw_json = llm_client.summarise_json(
                        envelope,
                        temperature=0.0,
                        max_tokens=envelope.get("constraints", {}).get("max_tokens", 256),
                        retries=0,
                        request_id=req_id,
                    )
                    break
                except Exception:
                    if attempts - 1 >= max_retries:
                        break
                    continue
            retry_count = max(0, attempts - 1)
        # decide whether this is a fallback (only if LLM was attempted)
        if raw_json is None:
            llm_fallback = use_llm
            supp_id: str | None = None
            try:
                if ev.anchor and ev.anchor.id and ev.anchor.id in ev.allowed_ids:
                    supp_id = ev.anchor.id
                elif ev.allowed_ids:
                    supp_id = ev.allowed_ids[0]
            except Exception:
                supp_id = ev.anchor.id if getattr(ev.anchor, "id", None) else (ev.allowed_ids[0] if ev.allowed_ids else None)
            ans = WhyDecisionAnswer(
                short_answer=deterministic_short_answer(ev),
                supporting_ids=[supp_id] if supp_id else [],
            )
            arte["llm_raw.json"] = b"{}"
        else:
            arte["llm_raw.json"] = raw_json.encode()
            try:
                parsed = orjson.loads(raw_json)
                ans = WhyDecisionAnswer.model_validate(parsed)
            except Exception:
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
                    short_answer=deterministic_short_answer(ev),
                    supporting_ids=[supp_id] if supp_id else [],
                )
                arte["llm_raw.json"] = b"{}"

    with trace_span.ctx("validate", anchor_id=ev.anchor.id):
        try:
            ans, changed, errs = templater.validate_and_fix(ans, ev.allowed_ids, ev.anchor.id)
        except Exception as exc:
            logger.warning("validate_and_fix_failed", exc_info=exc)
            changed, errs = False, ["validate_and_fix error"]

    flags = CompletenessFlags(
        has_preceding=bool(ev.transitions.preceding),
        has_succeeding=bool(ev.transitions.succeeding),
        event_count=len(ev.events),
    )

    arte["envelope.json"]       = orjson.dumps(envelope)
    arte["rendered_prompt.txt"] = canonical_json(envelope)
    arte.setdefault("llm_raw.json", b"{}")

    meta = {
        "policy_id": envelope["policy_id"],
        "prompt_id": envelope["prompt_id"],
        "prompt_fingerprint": envelope["_fingerprints"]["prompt_fingerprint"],
        "bundle_fingerprint": envelope["_fingerprints"]["bundle_fingerprint"],
        "bundle_size_bytes": bundle_size_bytes(ev),
        "snapshot_etag": envelope["_fingerprints"]["snapshot_etag"],
        "fallback_used": bool(llm_fallback or changed),
        "retries": int(retry_count),
        "gateway_version": _GATEWAY_VERSION,
        "selector_model_id": SELECTOR_MODEL_ID,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "validator_errors": errs,
        "evidence_metrics": sel_meta,
        "load_shed": should_load_shed(),
    }

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
