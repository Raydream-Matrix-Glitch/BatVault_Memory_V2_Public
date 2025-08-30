from __future__ import annotations
import time
from typing import Any, Dict, Optional
from core_logging import get_logger
from .logging_helpers import stage as log_stage
from core_config import get_settings
from ..http import fetch_json
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