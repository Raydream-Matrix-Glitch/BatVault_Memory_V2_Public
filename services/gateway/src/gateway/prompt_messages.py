from __future__ import annotations
from typing import Any, Dict, List
from core_utils import jsonx
from core_config import get_settings
from core_config.constants import (
    SHORT_ANSWER_MAX_CHARS as SHORT_ANSWER_CAP,
    SHORT_ANSWER_MAX_SENTENCES as SHORT_ANSWER_SENT_CAP,
)

def _resolve_caps() -> tuple[int, int]:
    """
    Prefer canonical settings; fall back to legacy, then constants.
    Returns (char_cap, sent_cap).
    """
    s = get_settings()
    try:
        char_cap = int(getattr(s, "short_answer_max_chars", None) or getattr(s, "answer_char_cap", None) or SHORT_ANSWER_CAP)
    except Exception:
        char_cap = SHORT_ANSWER_CAP
    try:
        sent_cap = int(getattr(s, "short_answer_max_sentences", None) or getattr(s, "answer_sentence_cap", None) or SHORT_ANSWER_SENT_CAP)
    except Exception:
        sent_cap = SHORT_ANSWER_SENT_CAP
    return max(1, char_cap), max(1, sent_cap)

def build_messages(envelope: Dict[str, Any]) -> List[Dict[str, str]]:
    """Render the chat *messages* we send to vLLM/TGI.

    The system prompt enforces a concise style for the ``answer.short_answer``
    returned by the LLM.  Specifically:

      • The assistant must return exactly one valid JSON object matching the
        schema contained in the user's message.
      • ``answer.short_answer`` should be no more than the configured number of sentences and obey the configured character cap.
      • When the decision maker and date are present in the evidence they
        should begin the short answer (e.g., "<Maker> on <YYYY-MM-DD>: ...").
      • If a "Next:" pointer is included, it should refer to the first
        succeeding transition.
      • Raw evidence IDs must never appear in the prose; cite them only in
        ``cited_ids``.

    These instructions help the post‑processing clamp to avoid unnecessary
    fallbacks due to style violations.
    """
    char_cap, sent_cap = _resolve_caps()
    system_text = (
        "You are a JSON-only assistant. Give exactly one valid JSON object "
        "conforming to the schema in the user message. Do NOT include code fences, "
        "extra fields or natural-language commentary. When constructing the "
        "answer.short_answer field, use no more than "
        f"{sent_cap} sentences and at most {char_cap} characters. Begin with the decision maker and date when available, "
        "add a brief 'Because ...' clause naming 1–2 drivers (no IDs), optionally include a 'Next:' sentence pointing to the first succeeding"
        "transition, and never include raw evidence IDs in the prose. Populate answer.cited_ids (preferred) with the IDs actually cited (subset of allowed_ids; anchor first). Legacy 'supporting_ids' is tolerated but will be ignored if 'cited_ids' is present."
    )
  
    return [
        {"role": "system", "content": system_text},
        # Use the repo's canonical JSON serializer for consistency across
        # fingerprints, logs and replay. (Avoid ad-hoc orjson usage.)
        {"role": "user", "content": jsonx.dumps(envelope)},
    ]
