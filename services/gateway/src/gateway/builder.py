from __future__ import annotations
import time, uuid
from typing import Any, Mapping
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
    plan_dict = {"anchor": ev.anchor.id, "k": 1}
    arte["plan.json"] = orjson.dumps(plan_dict)

    ev.allowed_ids = _allowed_ids(ev)

    # ── answer (templater + validator) ─────────────────────────
    ans = req.answer or WhyDecisionAnswer(
        short_answer=deterministic_short_answer(ev),
        supporting_ids=[ev.allowed_ids[0]] if ev.allowed_ids else [],
    )
    # ── validate ────────────────────────────────────────────────
    with trace_span.ctx("validate", anchor_id=ev.anchor.id):
        ans, changed, errs = templater.validate_and_fix(
            ans, ev.allowed_ids, ev.anchor.id,
        )

    # ── completeness flags ─────────────────────────────────────
    flags = CompletenessFlags(
        has_preceding=bool(ev.transitions.preceding),
        has_succeeding=bool(ev.transitions.succeeding),
        event_count=len(ev.events),
    )

    # ── canonical prompt envelope + fingerprint ────────────────
    envelope = build_prompt_envelope(
        question=f"Why was decision {ev.anchor.id} made?",
        evidence=ev.model_dump(mode="python"),
        snapshot_etag=getattr(ev, "snapshot_etag", "unknown"),
        intent=req.intent,
        allowed_ids=ev.allowed_ids,
        retries=getattr(ev, "_retry_count", 0),
    )
    arte["envelope.json"]       = orjson.dumps(envelope)
    arte["rendered_prompt.txt"] = canonical_json(envelope)

    # ── audit trail: raw-LLM payload (may be stub) ──────────────────────
    # Milestone-3 audit contract (§ M5 in the tech-spec) requires this
    # artefact for *every* request.  When the gateway is running in
    # “LLM-off / templater” mode we still persist an empty JSON object so
    # that the artefact set stays stable and downstream tooling (replay
    # viewer, CI tests) can rely on its presence.
    arte.setdefault("llm_raw.json", b"{}")

    retry_count = 2 if changed else 0

    # ── meta block ─────────────────────────────────────────────
    meta = {
        "policy_id": envelope["policy_id"],
        "prompt_id": envelope["prompt_id"],
        "prompt_fingerprint": envelope["_fingerprints"]["prompt_fingerprint"],
        "bundle_fingerprint": envelope["_fingerprints"]["bundle_fingerprint"],
        "bundle_size_bytes": bundle_size_bytes(ev),
        "snapshot_etag": envelope["_fingerprints"]["snapshot_etag"],
        "fallback_used": changed,
        "retries": retry_count,
        "gateway_version": _GATEWAY_VERSION,
        "selector_model_id": SELECTOR_MODEL_ID,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "validator_errors": errs,
        "evidence_metrics": sel_meta,
        "load_shed": should_load_shed(),
    }

    # ── final response object ──────────────────────────────────
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
