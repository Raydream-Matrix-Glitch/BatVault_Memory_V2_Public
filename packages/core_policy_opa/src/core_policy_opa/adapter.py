from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time
from urllib.parse import urlsplit
import httpx
from core_config import get_settings
from core_utils.jsonx import dumps as canonical_dumps
from core_logging import get_logger, log_stage, record_error, current_request_id
from core_observability.otel import inject_trace_context
from core_utils.identity import build_opa_input

logger = get_logger("core_policy_opa")

@dataclass(frozen=True)
class OPADecision:
    allowed_ids: List[str]
    extra_visible: List[str]
    denied_status: Optional[int]
    policy_fp: str
    explain: Optional[Dict[str, Any]] = None
    engine: str = "opa"

def _opa_enabled() -> bool:
    s = get_settings()
    return bool(getattr(s, "opa_url", None))

def _build_url() -> str:
    s = get_settings()
    base = s.opa_url.rstrip("/")
    path = (s.opa_decision_path or "/v1/data/batvault/decision").lstrip("/")
    return f"{base}/{path}"

def opa_decide_if_enabled(
    *,
    anchor_id: str,
    edges: List[Dict[str, Any]],
    headers: Dict[str, str],
    snapshot_etag: str,
    intents: Optional[List[str]] = None,
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
    input_obj = build_opa_input(
        anchor_id=anchor_id,
        edges=edges,
        headers=headers,
        snapshot_etag=snapshot_etag,
        intents=intents,
    )
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
        from core_http.client import fetch_json_sync
        body = fetch_json_sync(
            "POST", url,
            headers=req_headers,
            json={"input": input_obj},
            timeout_ms=int(timeout*1000),
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log_stage(
            logger, "http.client", "http.client.response", op="opa",
            http={
                "method": "POST",
                "target": urlsplit(url).path or "/",
                "status_code": 200,
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
    
    result = (body.get("result", body) or {}) if isinstance(body, dict) else {}
    raw_ids = list(result.get("allowed_ids") or [])
    explain = result.get("explain") if isinstance(result, dict) else None
    # Deterministic dedupe; keep order stable then lex-sort
    seen, ordered = set(), []
    for x in raw_ids:
        if x not in seen:
            ordered.append(x); seen.add(x)
    allowed_ids   = sorted(ordered)
    extra_visible = list(result.get("extra_visible") or [])
    denied_status = result.get("denied_status")
    policy_fp     = str(result.get("policy_fp") or "")
    if not policy_fp:
        record_error("OPA.ERROR", where="memory_api#opa_decide", message="missing policy_fp in OPA decision", logger=logger)
        return OPADecision(allowed_ids=[], extra_visible=[], denied_status=403, policy_fp="sha256:invalid", explain=explain)
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
        explain=explain,
    )