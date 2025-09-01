from __future__ import annotations
import time
from typing import Any, Dict, Optional
import re
from core_logging import get_logger
from .logging_helpers import stage as log_stage
from core_config import get_settings
from core_http.client import fetch_json
_logger = get_logger("gateway.llm")

def _pick_adapter(request_id: str, headers: Optional[Dict[str, str]]) -> tuple[str, str]:
    """Deterministically pick control/canary based on request_id and settings.

    Returns (cohort, endpoint_url)
    """
    s = get_settings()
    # Header override
    hdr_override = (headers or {}).get(getattr(s, 'canary_header_override', '') or '')
    if hdr_override and hdr_override.lower() in ('1','true','yes','on'):
        return 'canary', getattr(s, 'canary_model_endpoint', s.control_model_endpoint)
    # Percent split
    try:
        pct = max(0, min(100, int(getattr(s, 'canary_pct', 0))))
    except Exception:
        pct = 0
    # Simple stable hash on request_id
    h = 0
    for ch in (request_id or ''):
        h = (h * 131 + ord(ch)) % 10_000
    use_canary = (pct > 0) and (h % 100 < pct)
    if use_canary:
        return 'canary', getattr(s, 'canary_model_endpoint', s.control_model_endpoint)
    return 'control', getattr(s, 'control_model_endpoint', 'http://localhost:8000')

async def call(envelope: Dict[str, Any], *, request_id: str, headers: Optional[Dict[str,str]] = None) -> Any:
    """Unified LLM call with retries and structured logging.

    Centralises adapter selection, retries and telemetry. Uses the shared
    HTTP client via :func:`fetch_json`.
    """
    s = get_settings()
    retries = int(getattr(s, 'llm_retries', 2))
    cohort, url = _pick_adapter(request_id, headers)

    t0 = time.perf_counter()
    try:
        log_stage(_logger, 'llm', 'llm.attempt', request_id=request_id, adapter=cohort, endpoint=url)
    except Exception:
        pass

    try:
        resp = await fetch_json(
            'POST',
            f"{url.rstrip('/')}/generate",
            json=envelope,
            headers=headers,
            retry=retries,
            request_id=request_id,
            stage='llm',
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            log_stage('llm', 'llm.success', request_id=request_id, adapter=cohort, latency_ms=latency_ms, retry_count=retries)
        except Exception:
            pass
        return resp
    except Exception as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            log_stage('llm', 'llm.error', request_id=request_id, latency_ms=latency_ms, retry_count=retries, reason=type(e).__name__)
        except Exception:
            pass
        raise

# --- Enhanced last-call telemetry & unified llm_call -----------------------

# Public snapshot of the last LLM invocation (read by the builder).
LAST_CALL: dict = {}
# Backwards-compat export
_llm_last_call = LAST_CALL

def _reason_from_exception(exc: BaseException) -> str:
    name = type(exc).__name__
    try:
        import httpx
        if isinstance(exc, httpx.ConnectTimeout) or isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "endpoint_unreachable"
        if isinstance(exc, httpx.HTTPStatusError):
            return "http_error"
    except Exception:
        pass
    if name in {"JSONDecodeError", "ValueError"}:
        return "parse_error"
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()

# Canonicalize fallback reasons to a small, fixed enum so downstream
# contracts stay stable.
_ALLOWED_FALLBACKS = {
    "llm_off",
    "endpoint_unreachable",
    "timeout",
    "http_error",
    "parse_error",
    "stub_answer",
    "no_raw_json",
}
def _sanitize_reason(reason: str | None) -> str:
    if not reason:
        return "no_raw_json"
    r = str(reason).strip().lower()
    return r if r in _ALLOWED_FALLBACKS else ("http_error" if "http" in r else "no_raw_json")

async def _invoke_adapter(endpoint: str,
                          envelope: Dict[str, Any],
                          *,
                          temperature: float,
                          max_tokens: int) -> str:
    # Prefer vLLM/OpenAI adapter by default; use TGI when endpoint suggests it.
    try:
        from ..llm_adapters import vllm as _v
    except Exception:
        _v = None  # type: ignore
    try:
        from ..llm_adapters import tgi as _tgi
    except Exception:
        _tgi = None  # type: ignore
    if _tgi and (endpoint.endswith("/generate") or "tgi" in endpoint.lower()):
        return await _tgi.generate_async(endpoint, envelope, temperature=temperature, max_tokens=max_tokens)
    if _v:
        return await _v.generate_async(endpoint, envelope, temperature=temperature, max_tokens=max_tokens)
    return await fetch_json("POST", endpoint, json={"prompt": envelope}, stage="llm")  # type: ignore[return-value]

async def llm_call(envelope: Dict[str, Any],
                   *,
                   request_id: str,
                   headers: Optional[Dict[str, str]] = None,
                   retries: int = 0,
                   temperature: float = 0.0,
                   max_tokens: int = 512) -> Optional[str]:
    """Unified entry used by the builder; annotates LAST_CALL with details."""
    import time as _time
    t0 = _time.perf_counter()
    cohort, endpoint = _pick_adapter(request_id, headers)
    LAST_CALL.clear()
    LAST_CALL.update({
        "cohort": cohort,
        "endpoint": endpoint,
        "attempts": max(0, int(retries)) + 1,
        "latency_ms": 0,
        "error_code": None,
    })
    try:
        resp = await _invoke_adapter(endpoint, envelope, temperature=temperature, max_tokens=max_tokens)
        LAST_CALL["latency_ms"] = int((_time.perf_counter() - t0) * 1000)
        return resp
    except Exception as e:
        LAST_CALL["latency_ms"] = int((_time.perf_counter() - t0) * 1000)
        LAST_CALL["error_code"] = _sanitize_reason(_reason_from_exception(e))
        try:
            log_stage('llm', 'llm.error.annotated',
                      request_id=request_id,
                      endpoint=endpoint,
                      cohort=cohort,
                      reason=LAST_CALL["error_code"],
                      latency_ms=LAST_CALL["latency_ms"])
        except Exception:
            pass
        raise