"""
Server-Sent Events (SSE) helpers for streaming short answers and final payloads.

These helpers live in the core_utils package so they can be shared across
services without introducing circular dependencies.  They implement the same
chunking semantics that the Gateway previously defined locally.

Two public generators are provided:

* ``stream_chunks`` streams tokenised chunks of a plain string and terminates
  with ``[DONE]``.  When ``include_event`` is ``True`` each token is preceded
  by ``event: short_answer`` and the terminating chunk is preceded by
  ``event: done``.

* ``stream_answer_with_final`` streams tokenised chunks of a short answer,
  then emits a final JSON payload before terminating with ``[DONE]``.  It
  accepts the same ``include_event`` flag and chunk size as ``stream_chunks``.

See the Gateway README for high level design and usage details.
"""

from typing import Iterable
from . import jsonx


def _chunks(text: str, chunk_size: int) -> Iterable[str]:
    """
    Yield successive chunks of ``text`` of size ``chunk_size`` (>=1).
    The underlying text is converted to an empty string if ``None`` is passed.
    A minimal step of 1 is enforced to avoid division-by-zero errors.

    Args:
        text: The input string to chunk.  ``None`` is treated as ``""``.
        chunk_size: Desired length of each emitted chunk.  Values < 1
            default to 1.

    Yields:
        Non-empty substrings of ``text`` with length up to ``chunk_size``.
    """
    step = max(1, int(chunk_size))
    for idx in range(0, len(text or ""), step):
        chunk = (text or "")[idx : idx + step]
        if chunk:
            yield chunk


def stream_chunks(
    text: str,
    *,
    include_event: bool = False,
    chunk_size: int = 24,
) -> Iterable[str]:
    """
    Server‑Sent Events generator that streams a plain string one chunk at a time.

    It yields SSE lines conforming to the ``data:`` protocol.  Each chunk of the
    input text is wrapped in a small JSON object with a single ``token`` key.
    After all chunks have been sent the generator emits a terminal ``[DONE]``
    marker.  An optional ``event`` field is emitted before each data line to
    support client side event routing.

    Args:
        text: The string to stream.  ``None`` is treated as ``""``.
        include_event: If set, prepend ``event: short_answer`` to each token
            line and ``event: done`` to the final chunk.
        chunk_size: Desired length of each emitted chunk.  Defaults to 24.

    Yields:
        SSE formatted strings.  Each token is followed by a double newline
        per the SSE specification.
    """
    text = text or ""
    for token in _chunks(text, chunk_size):
        if include_event:
            yield "event: short_answer\n"
        yield f"data: {jsonx.dumps({'token': token})}\n\n"

    # Signal stream completion
    if include_event:
        yield "event: done\n"
    yield "data: [DONE]\n\n"


def stream_answer_with_final(
    short_answer: str,
    final_payload: dict,
    *,
    include_event: bool = False,
    chunk_size: int = 24,
) -> Iterable[str]:
    """
    Server‑Sent Events generator that streams a short answer followed by a final payload.

    This helper behaves like :func:`stream_chunks` for the short answer text, then
    emits the complete ``final_payload`` as a single SSE ``data:`` message before
    signalling completion with ``[DONE]``.  The ``include_event`` flag controls
    whether ``event:`` fields are emitted ahead of token and final messages.

    Args:
        short_answer: The LLM‑produced short answer.  ``None`` is treated as ``""``.
        final_payload: A dict representing the full response to send after the
            short answer tokens.  It will be JSON‑encoded via ``jsonx.dumps``.
        include_event: If set, prepend ``event: short_answer`` to each token
            line and ``event: done`` to the final chunk.
        chunk_size: Desired length of each emitted token.  Defaults to 24.

    Yields:
        SSE formatted strings streaming the short answer, the final payload,
        and a terminal marker.
    """
    short_answer = short_answer or ""
    for token in _chunks(short_answer, chunk_size):
        if include_event:
            yield "event: short_answer\n"
        yield f"data: {jsonx.dumps({'token': token})}\n\n"

    # Emit the full response object before termination
    yield f"data: {jsonx.dumps(final_payload)}\n\n"
    if include_event:
        yield "event: done\n"
    yield "data: [DONE]\n\n"