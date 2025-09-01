from __future__ import annotations
from typing import Any, Dict, List
from core_utils import jsonx

def build_messages(envelope: Dict[str, Any]) -> List[Dict[str, str]]:
    """Render the chat *messages* we send to vLLM/TGI.

    The system prompt enforces a concise style for the ``answer.short_answer``
    returned by the LLM.  Specifically:

      • The assistant must return exactly one valid JSON object matching the
        schema contained in the user's message.
      • ``answer.short_answer`` should be no more than two sentences and no
        longer than 320 characters.
      • When the decision maker and date are present in the evidence they
        should begin the short answer (e.g., "<Maker> on <YYYY-MM-DD>: ...").
      • If a "Next:" pointer is included, it should refer to the first
        succeeding transition.
      • Raw evidence IDs must never appear in the prose; cite them only in
        ``supporting_ids``.

    These instructions help the post‑processing clamp to avoid unnecessary
    fallbacks due to style violations.
    """
    system_text = (
        "You are a JSON-only assistant. Give exactly one valid JSON object "
        "conforming to the schema in the user message. Do NOT include code fences, "
        "extra fields or natural-language commentary. When constructing the "
        "answer.short_answer field, use no more than two sentences and at most "
        "320 characters. Begin with the decision maker and date when available, "
        "optionally include a 'Next:' sentence pointing to the first succeeding "
        "transition, and never include raw evidence IDs in the prose."
    )
    return [
        {"role": "system", "content": system_text},
        # Use the repo's canonical JSON serializer for consistency across
        # fingerprints, logs and replay. (Avoid ad-hoc orjson usage.)
        {"role": "user", "content": jsonx.dumps(envelope)},
    ]
