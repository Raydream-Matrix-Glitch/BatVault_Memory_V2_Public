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

    # Always honour stub mode first – used in CI & local dev
    if os.getenv("OPENAI_DISABLED", "1") == "1":
        return _stub()

    # ── Real OpenAI call (temperature=0, JSON‑only policy) ────────────────
    attempt = 0
    while attempt <= retries:
        try:
            import openai  # Local import keeps dep optional

            openai.api_key = os.getenv("OPENAI_API_KEY")
            resp = openai.ChatCompletion.create(
                model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo-0613"),
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": "JSON-only; emit WhyDecisionAnswer schema.",
                    },
                    {"role": "user", "content": orjson.dumps(envelope).decode()},
                ],
            )
            raw_json: str = resp.choices[0].message.content

            # Minimal sanity check – must parse without error
            orjson.loads(raw_json)
            return raw_json
        except Exception:
            time.sleep(0.2 * (attempt + 1))
            attempt += 1

    # Fallback after retries exhausted
    return _stub()