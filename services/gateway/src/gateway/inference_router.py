import json
import random
import time
from typing import Any, Dict, Optional, Tuple

from core_config import get_settings
from core_logging import get_logger
from .logging_helpers import stage as log_stage
from . import llm_adapters
from .metrics import gateway_llm_requests, gateway_llm_latency_ms

logger = get_logger("gateway.inference")
# Disable propagation to avoid duplicate log entries in the root logger.
logger.propagate = False

# Exposed for headers in /v2/* responses
last_call: Dict[str, Any] = {}

def _safety_clamp(envelope: Dict[str, Any], raw_json: str, *, request_id: Optional[str] = None) -> str:
    """Final clamp: enforce supporting_ids ⊆ allowed_ids and cap short_answer ≤320 chars.
    If parsing fails, return the original string unchanged.
    """
    try:
        data = json.loads(raw_json or "{}")
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
        out = json.dumps(data, separators=(",", ":"))
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

def _select_endpoint_and_adapter(cohort: str) -> Tuple[str, Any, str]:
    s = get_settings()
    if cohort == "canary":
        endpoint = getattr(s, "canary_model_endpoint", None) or ""
    else:
        endpoint = getattr(s, "control_model_endpoint", None) or ""
    # Heuristic: hosts containing "tgi" use the TGI adapter, otherwise vLLM.
    adapter = llm_adapters.tgi if "tgi" in endpoint else llm_adapters.vllm
    model_label = getattr(s, "vllm_model_name", None) or endpoint.rsplit("/", 1)[-1]
    return endpoint, adapter, model_label

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
    endpoint, adapter, model_label = _select_endpoint_and_adapter(cohort)
    temp = float(temperature if temperature is not None else getattr(s, "llm_temperature", 0.0))
    maxt = int(max_tokens or getattr(s, "llm_max_tokens", 512))

    attempt = 0
    start = time.perf_counter()
    status = "ok"
    try:
        while True:
            attempt += 1
            try:
                if adapter is llm_adapters.vllm:
                    raw = await adapter.generate_async(endpoint, envelope, temperature=temp, max_tokens=maxt)
                else:
                    raw = await adapter.generate_async(endpoint, envelope, temperature=temp, max_tokens=maxt)
                return _safety_clamp(envelope, raw, request_id=request_id)
            except Exception:
                if attempt > max(0, int(retries or 0)):
                    raise
                # jittered backoff: 50-200ms
                await __import__("asyncio").sleep(0.05 + (0.15 * (attempt % 3)))
    except Exception:
        status = "error"
        raise
    finally:
        dur_ms = (time.perf_counter() - start) * 1000.0
        last_call.clear()
        last_call.update({"model": model_label, "canary": (cohort == "canary"), "latency_ms": int(dur_ms)})
        try:
            gateway_llm_requests(model_label, "true" if cohort == "canary" else "false", status)
            gateway_llm_latency_ms(model_label, "true" if cohort == "canary" else "false", dur_ms)
            log_stage("inference", "call", request_id=request_id, latency_ms=int(dur_ms), status=status, retries=attempt-1)
        except Exception:
            pass

def call_llm_sync(*args, **kwargs):
    import anyio
    return anyio.run(lambda: call_llm(*args, **kwargs))