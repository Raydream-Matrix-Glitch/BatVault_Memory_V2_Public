"""
Canonical embeddings client used by Memory API and others.

Exposes:
    async def embed(texts: list[str]) -> list[list[float]] | None
"""
from typing import Iterable, List, Optional
import os, time

from core_logging import get_logger, log_stage, trace_span
from core_config.constants import timeout_for_stage
from core_observability.otel import inject_trace_context
from core_http.client import get_http_client

_logger = get_logger("core_ml.embeddings")
_logger.propagate = True

_enable = os.getenv("ENABLE_EMBEDDINGS", "false").lower() in {"1","true","yes"}
_endpoint = (os.getenv("EMBEDDINGS_ENDPOINT") or "http://tei-embed:8085").rstrip("/")
try:
    _dims = int(os.getenv("EMBEDDINGS_DIMS", "768"))
except Exception:
    _dims = 768

def _normalize_texts(texts: Iterable[str]) -> List[str]:
    out: List[str] = []
    for t in texts:
        s = (t or "").strip()
        if not s:
            continue
        out.append(s)
    return out

async def embed(texts: Iterable[str]) -> Optional[List[List[float]]]:
    if not _enable:
        return None
    items = _normalize_texts(texts)
    if not items:
        return []
    url = _endpoint + "/embed"
    # single shared client with stage-aware timeout
    client = get_http_client(timeout_ms=int(timeout_for_stage("enrich")*1000))
    payload = {"input": items}
    start = time.perf_counter()
    try:
        with trace_span("embeddings.call", stage="enrich"):
            resp = await client.post(url, json=payload, headers=inject_trace_context({}))
            resp.raise_for_status()
            data = resp.json()
            vecs = data.get("data") or data.get("embeddings") or []
            # Validate dims
            out: List[List[float]] = []
            for v in vecs:
                arr = v.get("embedding") if isinstance(v, dict) else v
                if isinstance(arr, list) and len(arr) == _dims:
                    out.append(arr)
            if len(out) != len(items):
                log_stage(_logger, "embeddings", "dims_mismatch", expected=_dims, got=len(out))
            return out if out else None
    except Exception as e:
        dur_ms = int((time.perf_counter() - start)*1000)
        try:
            name = type(e).__name__
            log_stage(_logger, "embeddings", "error", reason=name, latency_ms=dur_ms)
        except Exception:
            pass
        return None