from __future__ import annotations

import os, aiohttp, asyncio, time, threading, logging
from typing import Any, List, Dict, Optional

import orjson

MAX_LEN = 320  # hard limit for short_answer length


# --------------------------------------------------------------------------- #
#  Logging, routing, and concurrency guards                                   #
# --------------------------------------------------------------------------- #

try:
    from core_logging import log_stage  # structured logging hook
except Exception:  # pragma: no cover – defensive noop if not present
    def log_stage(*_a, **_kw):  # type: ignore
        return None

# Import llm_router defensively (package vs. module layout)
try:  # try absolute import first
    import llm_router  # type: ignore
except Exception:  # pragma: no cover
    try:
        from . import llm_router  # type: ignore
    except Exception:
        llm_router = None  # type: ignore

logger = logging.getLogger(__name__)
_LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "2"))
_LLM_SEMAPHORE = threading.BoundedSemaphore(_LLM_MAX_CONCURRENCY)

# --------------------------------------------------------------------------- #
#  Legacy helper (kept for backward compatibility)                            #
# --------------------------------------------------------------------------- #


def summarise(prompt: str) -> str:  # pragma: no cover – deprecated
    """Return *short_answer* text only (legacy)."""
    raw: str = summarise_json(prompt)
    return orjson.loads(raw)["short_answer"]


# --------------------------------------------------------------------------- #
#  Primary helper – JSON‑only                                                 #
# --------------------------------------------------------------------------- #


def summarise_json(
    envelope: Any,
    *,
    temperature: float = 0.0,
    max_tokens: int = 256,
    retries: int = 2,
    request_id: str | None = None,
    messages_override: Optional[List[Dict[str, str]]] = None,
    max_tokens_override: Optional[int] = None,
) -> str:
    """Return **JSON‑ONLY** string with ``short_answer`` & ``supporting_ids``.

    Behaviour:

    * **Stub mode** (*default* – ``OPENAI_DISABLED=1``): returns deterministic
      JSON so tests remain fully reproducible.
    * **Live mode**: performs a real OpenAI ChatCompletion call.  On errors the
      helper retries *retries* times, then falls back to the stub.
    """

    if isinstance(envelope, str):
        prompt_txt = envelope
        allowed_ids = []
    elif isinstance(envelope, dict):
        prompt_txt = envelope.get("question", "")
        # NOTE: allowed_ids live under envelope["evidence"]["allowed_ids"]
        ev = envelope.get("evidence", {}) or {}
        allowed_ids = (ev.get("allowed_ids", []) or
                       envelope.get("allowed_ids", []) or [])
    else:  # generic fallback
        prompt_txt = str(envelope)
        allowed_ids = []

    def _stub() -> str:
        summary = (f"STUB ANSWER: {prompt_txt}")[:MAX_LEN]
        return orjson.dumps({
            "short_answer": summary,
            "supporting_ids": allowed_ids[:1],
        }).decode()

    # Honour stub/disabled mode – when OPENAI_DISABLED is not "0" we always
    # return a deterministic stub. Also log this explicitly for auditability.
    _disabled = os.getenv("OPENAI_DISABLED", "1")
    if _disabled != "0":
        try:
            log_stage(logger, "llm", "disabled",
                      request_id=request_id, reason="env_gate", value=_disabled)
        except Exception:
            pass
        return _stub()

    # ── Real LLM call via the router (JSON-only) ────────────────────────
    if llm_router is None:
        # router module unavailable – deterministic fallback and log
        try:
            log_stage(logger, "llm", "fallback",
                      request_id=request_id, reason="router_unavailable")
        except Exception:
            pass
        return _stub()

    # Concurrency guard: ensures at most N calls in-flight to protect RAM.
    t0 = time.perf_counter()
    try:
        try:
            log_stage(logger, "llm", "queue_wait",
                      request_id=request_id, limit=_LLM_MAX_CONCURRENCY)
        except Exception:
            pass
        _LLM_SEMAPHORE.acquire()
        waited_ms = int((time.perf_counter() - t0) * 1000)
        try:
            log_stage(logger, "llm", "queue_acquired",
                      request_id=request_id, waited_ms=waited_ms)
        except Exception:
            pass

        raw_json = llm_router.call_llm(
            envelope if isinstance(envelope, dict)
            else {"question": prompt_txt, "allowed_ids": allowed_ids},
            request_id=request_id,
            headers=None,
            retries=retries,
            messages_override=messages_override,
            max_tokens_override=max_tokens_override if isinstance(max_tokens_override, int) else max_tokens,
        )
        # Basic parse validation – raises ValueError on malformed JSON
        orjson.loads(raw_json)
        try:
            log_stage(logger, "llm", "call_ok",
                      request_id=request_id, bytes=len(raw_json))
        except Exception:
            pass
        return raw_json
    except Exception as e:
        try:
            log_stage(logger, "llm", "call_fail",
                      request_id=request_id, reason=type(e).__name__)
            log_stage(logger, "llm", "fallback",
                      request_id=request_id, reason="exception")
        except Exception:
            pass
        return _stub()
    finally:
        try:
            _LLM_SEMAPHORE.release()
            log_stage(logger, "llm", "queue_release", request_id=request_id)
        except Exception:
            pass