"""
Client for TEI (Text Embeddings Inference) used by the Memory API.

This module wraps the remote embedding service and adds retry/backoff,
dimensionality checking and structured logging.  It exposes a single
function ``embed(texts: list[str])`` which returns a list of embedding
vectors when successful, or ``None`` if the service is disabled or an
error occurs.  Batch requests are supported.

Environment variables
---------------------
``ENABLE_EMBEDDINGS``: set to ``true`` to enable embedding calls; otherwise
    the embed function returns ``None`` immediately.
``EMBEDDINGS_ENDPOINT``: base URL of the TEI service.
``EMBEDDINGS_DIMS``: expected dimensionality of embeddings (e.g. 768).
"""

from __future__ import annotations

import os
import time
from typing import Iterable, List, Optional

import httpx

from core_logging import get_logger, log_stage, trace_span
import asyncio
import random

# lazily import OTEL header injection; if the observability module is absent this becomes a no‑op
try:
    from core_observability.otel import inject_trace_context  # type: ignore
except Exception:
    inject_trace_context = None  # type: ignore

_logger = get_logger("memory_api.embeddings_client")
_logger.propagate = True

# Cache configuration at module import time
#
# Embedding calls are gated via the ENABLE_EMBEDDINGS flag.  These values
# are resolved once at import time to avoid re-reading environment
# variables on every request.  See ``embed`` for additional handling.
_enable_embeddings = os.getenv("ENABLE_EMBEDDINGS", "false").lower() in {"1", "true", "yes"}
_endpoint = os.getenv("EMBEDDINGS_ENDPOINT", "http://tei-embed:8085").rstrip("/")
try:
    _dims = int(os.getenv("EMBEDDINGS_DIMS", "768"))
except ValueError:
    _dims = 768

# How many times to retry embedding calls on failure.  Retries use a
# simple exponential backoff with jitter (capped at a small window)
# to avoid thundering herds.  A value of 0 disables retries.
_MAX_RETRIES = int(os.getenv("EMBEDDINGS_MAX_RETRIES", "2"))

# Base delay (in seconds) for the first retry.  Subsequent retries
# double this delay.  A small amount of random jitter is added.
_BASE_DELAY = float(os.getenv("EMBEDDINGS_BASE_DELAY_SEC", "0.1"))


async def embed(texts: Iterable[str]) -> Optional[List[List[float]]]:
    """
    Retrieve embeddings for a batch of input strings.  When embeddings are
    disabled via ``ENABLE_EMBEDDINGS``, this function returns ``None``
    immediately to allow callers to fall back to BM25.

    Parameters
    ----------
    texts: Iterable[str]
        Sentences to embed.  Should not be empty.

    Returns
    -------
    list[list[float]] | None
        A list of embedding vectors on success, or ``None`` on failure or
        when embeddings are disabled.
    """
    # Do not attempt embeddings if disabled
    if not _enable_embeddings:
        return None
    batch = list(texts)
    if not batch:
        return []
    last_exc: Exception | None = None
    # Attempt embedding retrieval with simple retry/backoff
    for attempt in range(_MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Create a span for each embedding call to tie metrics and logs back
                # to the overarching trace. Attach low‑cardinality attributes for debugging.
                with trace_span("memory_api.embeddings.call", stage="embeddings") as sp:
                    try:
                        sp.set_attribute("endpoint", f"{_endpoint}/embeddings")
                        sp.set_attribute("batch_size", len(batch))
                        sp.set_attribute("dims", _dims)
                        sp.set_attribute("attempt", attempt)
                    except Exception:
                        pass
                    # propagate current trace context into the downstream HTTP call when possible
                    hdrs: dict[str, str] = {"Accept": "application/json"}
                    try:
                        if inject_trace_context:
                            hdrs = inject_trace_context(hdrs)
                    except Exception:
                        pass
                    resp = await client.post(
                        f"{_endpoint}/embeddings",
                        json={"input": batch},
                        headers=hdrs,
                    )
                resp.raise_for_status()
                j = resp.json()
                data = j.get("data") or []
                embeddings: List[List[float]] = [d.get("embedding") for d in data]
                # Validate dimensions
                if not embeddings or any(len(vec) != _dims for vec in embeddings):
                    log_stage(
                        _logger,
                        "embeddings",
                        "dims_mismatch",
                        dims=_dims,
                        dims_seen=(len(embeddings[0]) if embeddings else 0),
                    )
                    return None
                dt_ms = int((time.perf_counter() - start) * 1000)
                log_stage(
                    _logger,
                    "embeddings",
                    "fetched",
                    batch_size=len(batch),
                    dims=_dims,
                    latency_ms=dt_ms,
                )
                return embeddings
        except Exception as exc:
            last_exc = exc
            # log failure for this attempt
            log_stage(
                _logger,
                "embeddings",
                "error",
                error=type(exc).__name__,
                message=str(exc),
                attempt=attempt,
            )
            # on retryable error, wait before next attempt (if any)
            if attempt < _MAX_RETRIES:
                # exponential backoff: base * 2^attempt with jitter up to base
                delay = _BASE_DELAY * (2 ** attempt)
                jitter = _BASE_DELAY * random.random()
                await asyncio.sleep(delay + jitter)
            else:
                break
    # out of retries – return None
    return None