from __future__ import annotations

import os, time
from typing import Any

import orjson

MAX_LEN = 320  # hard limit for short_answer length

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
        allowed_ids = envelope.get("allowed_ids", []) or []
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
    # return a deterministic stub.  A missing or empty value is treated as
    # disabled.  This allows unit‑tests to force the real OpenAI retry path
    # by setting OPENAI_DISABLED=0.
    if os.getenv("OPENAI_DISABLED", "1") != "0":
        return _stub()

    # ── Real LLM call via the router (JSON-only) ────────────────────────
    try:
        raw_json = llm_router.call_llm(
            envelope if isinstance(envelope, dict) else {"question": prompt_txt, "allowed_ids": allowed_ids},
            request_id=request_id,
            headers=None,
            retries=retries,
        )
        # Basic parse validation – raises ValueError on malformed JSON
        orjson.loads(raw_json)
        return raw_json
    except Exception:
        # fallback to deterministic stub
        return _stub()