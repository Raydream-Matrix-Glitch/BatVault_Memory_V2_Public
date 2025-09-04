import random
import time
from typing import Any, Dict, Optional, Tuple
from core_utils import jsonx  # canonical JSON serializer
from core_utils.backoff import sleep_with_jitter
from core_config import get_settings
from core_logging import get_logger
from .logging_helpers import stage as log_stage
from . import llm_adapters
from .metrics import gateway_llm_requests, gateway_llm_latency_ms

logger = get_logger("gateway.inference")
# Disable propagation to avoid duplicate log entries in the root logger.
logger.propagate = False

# Give logs a readable adapter name
def _adapter_name(adapter: Any) -> str:
    try:
        return getattr(adapter, "__name__", adapter.__class__.__name__)
    except Exception:
        return str(adapter)

# Exposed for headers in /v2/* responses
last_call: Dict[str, Any] = {}

# Canonicalize fallback reasons so downstream contracts stay stable.
_ALLOWED_FALLBACKS = {
    "llm_off","endpoint_unreachable","timeout","http_error","parse_error","stub_answer","no_raw_json","llm_unavailable",
}
def sanitize_fallback_reason(reason: Optional[str]) -> str:
    if not reason:
        return "llm_unavailable"
    r = str(reason).strip().lower()
    if r in _ALLOWED_FALLBACKS:
        return r
    return "http_error" if "http" in r else "llm_unavailable"

def _safety_clamp(envelope: Dict[str, Any], raw_json: str, *, request_id: Optional[str] = None) -> str:
    """Final clamp: enforce supporting_ids ⊆ allowed_ids and cap short_answer ≤ 320 characters.
    If parsing fails, return the original string unchanged.  Uses the shared
    JSON helpers to guarantee canonical handling.
    """
    try:
        # Always parse using the shared JSON loader; this enforces sorted keys and
        # consistent unicode handling across services.  When the payload is not
        # a mapping, fall through to return the original string unchanged.
        data = jsonx.loads(raw_json or "{}")
        if not isinstance(data, dict):
            return raw_json
        allowed = set((envelope or {}).get("allowed_ids") or [])
        # supporting_ids
        sup = data.get("supporting_ids") or data.get("answer", {}).get("supporting_ids")
        if isinstance(sup, list):
            filtered = [x for x in sup if x in allowed]
            if "answer" in data and isinstance(data["answer"], dict):
                data["answer"]["supporting_ids"] = filtered
            else:
                data["supporting_ids"] = filtered
        # short_answer trim
        short = data.get("short_answer") or (data.get("answer", {}) or {}).get("short_answer")
        if isinstance(short, str) and len(short) > 320:
            short = short[:320].rstrip()
            if "answer" in data and isinstance(data["answer"], dict):
                data["answer"]["short_answer"] = short
            else:
                data["short_answer"] = short
        out = jsonx.dumps(data)
        try:
            log_stage("inference", "safety_clamp", request_id=request_id, clamped=False)
        except Exception:
            pass
        return out
    except Exception:
        try:
            log_stage("inference", "safety_clamp", request_id=request_id, clamped=False)
        except Exception:
            pass
        return raw_json

def _choose_cohort() -> str:
    s = get_settings()
    if not getattr(s, "canary_enabled", True):
        return "control"
    pct = max(0, min(100, getattr(s, "canary_pct", 0)))
    if pct <= 0:
        return "control"
    return "canary" if random.randint(1, 100) <= pct else "control"

def _select_endpoint_and_adapter(cohort: str, *, request_id: Optional[str] = None) -> Tuple[str, Any, str, str]:
    s = get_settings()
    if cohort == "canary":
        endpoint = getattr(s, "canary_model_endpoint", None) or ""
    else:
        endpoint = getattr(s, "control_model_endpoint", None) or ""
    # Heuristic: hosts containing "tgi" use the TGI adapter, otherwise vLLM.
    adapter = llm_adapters.tgi if "tgi" in endpoint else llm_adapters.vllm
    model_label = getattr(s, "vllm_model_name", None) or endpoint.rsplit("/", 1)[-1]
    adapter_label = "tgi" if adapter is llm_adapters.tgi else "vllm"
    try:
        log_stage("inference", "adapter_selected",
                  request_id=request_id,
                  cohort=cohort,
                  endpoint=endpoint,
                  adapter=adapter_label,
                  model=model_label)
    except Exception:
        pass
    return endpoint, adapter, model_label, adapter_label

async def call_llm(
    envelope: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    retries: int = 0,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    canary: Optional[bool] = None,
) -> str:
    s = get_settings()
    cohort = "canary" if canary else _choose_cohort()
    endpoint, adapter, model_label, adapter_label = _select_endpoint_and_adapter(cohort)
    # Record selection early for diagnostics and the Audit Drawer
    try:
        last_call.clear()
        last_call.update({
            "model": model_label,
            "canary": (cohort == "canary"),
            "endpoint": endpoint,
            "adapter": _adapter_name(adapter),
        })
        log_stage("inference", "adapter_selected",
                  endpoint=endpoint, adapter=_adapter_name(adapter),
                  model=model_label, cohort=cohort)
    except Exception:
        pass
    temp = float(temperature if temperature is not None else getattr(s, "llm_temperature", 0.0))
    maxt = int(max_tokens or getattr(s, "llm_max_tokens", 512))

    attempt = 0
    start = time.perf_counter()
    status = "ok"
    try:
        try:
            log_stage("inference", "invoke", request_id=request_id, endpoint=endpoint, retries=int(retries or 0))
        except Exception:
            pass
        while True:
            attempt += 1
            try:
                if not hasattr(adapter, "generate_async"):
                    last_call["error_code"] = "adapter_missing_generate_async"
                    log_stage("inference", "dispatch_exception",
                              exception="AttributeError",
                              msg="Adapter missing generate_async",
                              endpoint=endpoint, adapter=_adapter_name(adapter))
                    raise AttributeError("Adapter has no generate_async")
                raw = await adapter.generate_async(endpoint, envelope, temperature=temp, max_tokens=maxt)
                return _safety_clamp(envelope, raw, request_id=request_id)
            except Exception as e:
                # Record attempt & a stable, canonical reason for observability
                last_call["attempt"] = attempt
                try:
                    from httpx import ConnectTimeout, ReadTimeout, ConnectError, HTTPStatusError  # type: ignore
                    if isinstance(e, (ConnectTimeout, ReadTimeout)):
                        last_call["error_code"] = "timeout"
                    elif isinstance(e, ConnectError):
                        last_call["error_code"] = "endpoint_unreachable"
                    elif isinstance(e, HTTPStatusError):
                        last_call["error_code"] = "http_error"
                    else:
                        last_call["error_code"] = "llm_unavailable"
                except Exception:
                    last_call["error_code"] = "llm_unavailable"
                try:
                    ex_name = getattr(e, "__class__", type(e)).__name__
                    log_stage("inference", "dispatch_exception",
                              exception=ex_name, msg=str(e),
                              request_id=request_id, cohort=cohort,
                              endpoint=endpoint, adapter=_adapter_name(adapter))
                except Exception:
                    pass
                try:
                    log_stage("inference", "error",
                              request_id=request_id, cohort=cohort, endpoint=endpoint,
                              reason=last_call["error_code"])
                except Exception:
                    pass
                # Special-case: vLLM 400 when max_tokens > remaining context.
                try:
                    from httpx import HTTPStatusError  # type: ignore
                except Exception:
                    HTTPStatusError = Exception  # type: ignore
                if isinstance(e, HTTPStatusError) and getattr(getattr(e, "response", None), "status_code", None) == 400:
                    # Try to parse remaining tokens from the error message, else fall back to halving.
                    try:
                        body_text = getattr(e.response, "text", "") or ""
                        import re as _re
                        m = _re.search(r"maximum context length is (\d+).*?request has (\d+) input tokens",
                                       body_text, _re.I | _re.S)
                        if m:
                            max_ctx = int(m.group(1)); used = int(m.group(2))
                            # Leave a small safety margin of 16 tokens.
                            remaining = max(1, max_ctx - used - 16)
                            new_maxt = max(16, min(maxt, remaining))
                        else:
                            new_maxt = max(16, int(maxt * 0.5))
                    except Exception:
                        new_maxt = max(16, int(maxt * 0.5))
                    if new_maxt < maxt:
                        try:
                            log_stage("inference", "token_clamp_retry",
                                      request_id=request_id, from_tokens=int(maxt), to_tokens=int(new_maxt))
                        except Exception:
                            pass
                        maxt = new_maxt
                        # immediate retry without additional backoff
                        continue
                if attempt > max(0, int(retries or 0)):
                    raise
                # jittered backoff: 50–200ms
                await sleep_with_jitter(attempt)
    except Exception:
        status = "error"
        raise
    finally:
        dur_ms = (time.perf_counter() - start) * 1000.0
        _err = last_call.get("error_code")
        last_call.clear()
        # Preserve error code if one was observed during attempts
        last_call.update({
            "model": model_label,
            "canary": (cohort == "canary"),
            "latency_ms": int(dur_ms),
            "endpoint": endpoint,
            "adapter": adapter_label,
            **({"error_code": _err} if _err else {}),
        })
        try:
            gateway_llm_requests(model_label, "true" if cohort == "canary" else "false", status)
            gateway_llm_latency_ms(model_label, "true" if cohort == "canary" else "false", dur_ms)
            log_stage("inference", "call", request_id=request_id, latency_ms=int(dur_ms), status=status, retries=attempt-1)
        except Exception:
            pass

def call_llm_sync(*args, **kwargs):
    import anyio
    return anyio.run(lambda: call_llm(*args, **kwargs))

# ---- Backwards-compatible aliases (keep builder imports working) ----
llm_call = call_llm
llm_call_sync = call_llm_sync