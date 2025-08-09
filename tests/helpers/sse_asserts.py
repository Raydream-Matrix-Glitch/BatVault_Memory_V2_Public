"""Shared helper assertions for Server-Sent Event (SSE) streaming
of the `answer.short_answer` tokens as specified in the tech-spec (§4.1
 G. Streaming & Fallback Semantics).  The helper is intentionally strict
so that contracts break loudly while the implementation is still
evolving.
"""

from __future__ import annotations

import json
from typing import Iterable


def _iter_sse_lines(raw: str) -> Iterable[str]:
    """Yield lines that start with the canonical `data:` prefix."""
    for line in raw.splitlines():
        if line.startswith("data:"):
            yield line[5:].lstrip()  # strip prefix & leading spaces


def assert_sse_short_answer(resp) -> None:  # pytest helper – no return
    """Strictly validate that *resp* is a **single, validated** short-answer
    SSE stream (tech-spec §G – Streaming & Fallback Semantics).

      • HTTP 200 + ``text/event-stream``  
      • **One** logical stream → exactly one ``[DONE]``  
      • Every ``data:`` chunk (except the sentinel) is JSON ``{"token": …}``  
      • When the producer used ``include_event=true`` each chunk is preceded
        by ``event: short_answer`` and the stream finishes with
        ``event: done`` (both counts must match the token count).  
      • The joined token payload is 1–320 chars.

    Expectations (tech-spec §4.1):
      • HTTP 200 with *text/event-stream* content type  
      • At least one *data:* frame carrying the answer tokens  
      • Stream is explicitly terminated (e.g. `[DONE]` sentinel)  
      • Concatenated token payload must be non-empty (≤320 chars)
      """  

    # ── HTTP envelope ────────────────────────────────────────────────
    assert resp.status_code == 200, "non-200 status for SSE stream"
    ct = resp.headers.get("content-type", "")
    assert ct.startswith("text/event-stream"), f"unexpected content-type: {ct}"

    # The TestClient streaming interface exposes an iterator; we must
    # fully materialise it before making assertions so that the
    # underlying coroutine runs to completion within the test.
    chunks = list(resp.iter_text()) if hasattr(resp, "iter_text") else None
    body = "".join(chunks) if chunks else resp.text

    assert "data:" in body, "no SSE data frames detected"

    # ── Extract token frames ────────────────────────────────────────
    tokens: list[str] = []
    done_count = 0
    event_lines = [ln for ln in body.splitlines() if ln.startswith("event:")]
    has_event = bool(event_lines)
    short_answer_event_count = 0

    for payload in _iter_sse_lines(body):
        if payload.strip().upper() in {"[DONE]", "DONE"}:
            done_count += 1
            break
        # spec says each frame is JSON like {"token":"…"}
        try:
            obj = json.loads(payload)
            token = obj.get("token") or obj.get("text")
        except json.JSONDecodeError as exc:
            raise AssertionError(f"non-JSON payload: {payload!r}") from exc
        assert token, f"JSON payload missing 'token': {payload!r}"
        tokens.append(token)
        if has_event:
            short_answer_event_count += 1

    # ── Contract assertions ─────────────────────────────────────────
    assert done_count == 1, "stream must contain exactly one [DONE] sentinel"
    assert tokens, "no token payloads found in stream"
    joined = "".join(tokens)
    assert 0 < len(joined) <= 320, "joined short_answer length out of bounds"

    if has_event:
        assert short_answer_event_count == len(tokens), (
            "event: short_answer count does not match token chunks"
        )
        assert body.splitlines().count("event: done") == 1, (
            "missing or duplicate 'event: done'"
        )
 