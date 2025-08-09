"""
Utility for Server-Sent Event (SSE) streaming used by the Gateway
service.

`stream_chunks` slices a short answer into small pieces and sends them
to the client in the exact format required by the tech-spec (§4.1).

Default output (suitable for the existing tests):

    data: {"token": "<chunk>"}\n\n
    ...
    data: [DONE]\n\n

Optionally, set `include_event=True` to add named events:

    event: short_answer
    data: {"token": "<chunk>"}\n\n
    ...
    event: done
    data: [DONE]\n\n
"""

from __future__ import annotations

import json
from typing import Generator


def stream_chunks(
    text: str,
    *,
    chunk_size: int = 32,
    include_event: bool = False,
) -> Generator[str, None, None]:
    """
    Yield SSE frames for *text* in ≤ *chunk_size*-character slices.

    Parameters
    ----------
    text:
        The full answer string to stream (≤ 320 chars per spec).
    chunk_size:
        Maximum characters per slice (defaults to 32).
    include_event:
        If True, prepend ``event: short_answer`` to each chunk and finish
        with ``event: done``.  If False (default), emit only ``data:``
        lines.

    Returns
    -------
    Generator[str, None, None]
        A lazy iterator of UTF-8 text chunks suitable for
        `StreamingResponse`.
    """
    for idx in range(0, len(text), chunk_size):
        chunk = text[idx : idx + chunk_size]
        if not chunk:
            continue

        if include_event:
            yield "event: short_answer\n"

        # Each SSE record ends with a blank line (double LF).
        yield f"data: {json.dumps({'token': chunk})}\n\n"

    # Stream completion
    if include_event:
        yield "event: done\n"

    yield "data: [DONE]\n\n"
