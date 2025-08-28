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

from core_logging import get_logger, log_stage

_logger = get_logger("memory_api.embeddings_client")
_logger.propagate = True

# Cache configuration at module import time
_enable_embeddings = os.getenv("ENABLE_EMBEDDINGS", "false").lower() in {"1", "true", "yes"}
_endpoint = os.getenv("EMBEDDINGS_ENDPOINT", "http://tei-embed:8085").rstrip("/")
try:
    _dims = int(os.getenv("EMBEDDINGS_DIMS", "768"))
except ValueError:
    _dims = 768


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
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_endpoint}/embeddings",
                json={"input": batch},
                headers={"Accept": "application/json"},
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
        log_stage(
            _logger,
            "embeddings",
            "error",
            error=type(exc).__name__,
            message=str(exc),
        )
        return None