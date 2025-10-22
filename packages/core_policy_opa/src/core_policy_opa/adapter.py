from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time
from urllib.parse import urlsplit
import hashlib
import httpx
from core_config import get_settings
from core_utils.jsonx import dumps as canonical_dumps
from core_logging import get_logger, log_stage, record_error, current_request_id
from core_observability.otel import inject_trace_context

logger = get_logger("core_policy_opa")

@dataclass(frozen=True)
class OPADecision:
    allowed_ids: List[str]
    extra_visible: List[str]
    denied_status: Optional[int]
    policy_fp: str
    engine: str = "opa"

def _opa_enabled() -> bool:
    s = get_settings()
    return bool(getattr(s, "opa_url", None))

def _build_url() -> str:
    s = get_settings()
    base = s.opa_url.rstrip("/")
    path = (s.opa_decision_path or "/v1/data/batvault/decision").lstrip("/")
    return f"{base}/{path}"

def _derive_policy_fp(decision: Dict[str, Any]) -> str:
    # Prefer engine-provided fingerprint; else bundle SHA; else canonical decision hash.
    s = get_settings()
    fp = (
        str(decision.get("policy_fingerprint") or "")
        or str(getattr(s, "opa_bundle_sha", "") or "")
    )
    if fp:
        if not fp.startswith("sha256:"):
            return "sha256:" + fp  # normalize
        return fp
    payload = {
        "allowed_ids": decision.get("allowed_ids", []),
        "extra_visible": decision.get("extra_visible", []),
        "ruleset": decision.get("ruleset"),
    }
    return "sha256:" + hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()

def opa_decide_if_enabled(
    *,
    anchor_id: str,
    edges: List[Dict[str, Any]],
    headers: Dict[str, str],
    snapshot_etag: str,
) -> Optional[OPADecision]:
    """
    Call OPA when configured, else return None (explicit fallback).
    Narrow error handling:
      - HTTP/network errors: logged once per request_id then return None
      - Unexpected payload shape: logged and return None
    Deterministic: dedupes/lex-sorts allowed_ids.
    """
    if not _opa_enabled():
        return None

    url = _build_url()
    input_obj = {
        "anchor_id": anchor_id,
        "edges": edges,
        "headers": headers,
        "snapshot_etag": snapshot_etag,
    }
    # Sync client (Memory path is sync at this point); bounded timeout from settings.
    s = get_settings()
    timeout = max(0.1, float(getattr(s, "opa_timeout_ms", 1000)) / 1000.0)

    try:
        # Include trace context + request id for end-to-end correlation
        req_headers: Dict[str, str] = inject_trace_context({"content-type": "application/json"})
        rid = current_request_id() or None
        if rid:
            req_headers.setdefault("x-request-id", rid)
        # Emit an outbound request crumb with a redacted traceparent for OPA trace verification
        _tp = req_headers.get("traceparent")
        _tp_redacted = (_tp[:8] + "..." + _tp[-8:]) if isinstance(_tp, str) and len(_tp) > 16 else _tp
        log_stage(
            logger, "http.client", "http.client.request", op="opa",
            http={"method": "POST", "target": urlsplit(url).path or "/"},
            traceparent=_tp_redacted, request_id=rid
        )
        t0 = time.perf_counter()
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json={"input": input_obj}, headers=req_headers)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        log_stage(
            logger, "http.client", "http.client.response", op="opa",
            http={
                "method": "POST",
                "target": urlsplit(url).path or "/",
                "status_code": resp.status_code,
            },
            latency_ms=int(dt_ms), request_id=rid
        )
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
        # Deterministic error: protocol/transport only (policy denies are not errors)
        record_error(
            "OPA.ERROR",
            where="memory_api#opa_decide",
            message="OPA request failed",
            logger=logger,
            context={"error": str(exc), "url": url, "timeout_s": timeout},
        )
        return None

    try:
        body = resp.json()
    except ValueError as exc:
        record_error(
            "OPA.ERROR",
            where="memory_api#opa_decide",
            message="OPA response parse error",
            logger=logger,
            context={"error": str(exc)},
        )
        return None
    result = (body.get("result", body) or {}) if isinstance(body, dict) else {}
    raw_ids = list(result.get("allowed_ids") or [])
    # Deterministic dedupe; keep order stable then lex-sort
    seen, ordered = set(), []
    for x in raw_ids:
        if x not in seen:
            ordered.append(x); seen.add(x)
    allowed_ids   = sorted(ordered)
    extra_visible = list(result.get("extra_visible") or [])
    denied_status = result.get("denied_status")
    policy_fp     = _derive_policy_fp(result)
    # Emit a single normalized decision line; no duplicate raw logger calls.
    rid = current_request_id()
    if allowed_ids:
        log_stage(
            logger, "policy", "policy.decision",
            request_id=rid, effect="allow",
            allowed_count=len(allowed_ids),
            policy_fp=policy_fp, snapshot_etag=snapshot_etag,
        )
    else:
        status = int(denied_status or 403)
        log_stage(
            logger, "policy", "policy.decision",
            request_id=rid, effect="deny", status_code=status,
            policy_fp=policy_fp, snapshot_etag=snapshot_etag,
        )
    # Keep return shape unchanged
    logger.debug("opa_decide_complete", extra={
        "allowed_count": len(allowed_ids),
        "policy_fp": policy_fp,
    })
    return OPADecision(
        allowed_ids=allowed_ids,
        extra_visible=extra_visible,
        denied_status=denied_status,
        policy_fp=policy_fp,
    )