from __future__ import annotations
import hashlib, inspect
from hashlib import blake2s
from typing import Any, Optional
from core_utils import jsonx
from core_logging import get_logger, log_stage, current_request_id

_IDEM_LOGGER = get_logger("core_idem")

# Public: keep this small and dependency-light.
IDEM_TTL_SEC: int = 24 * 60 * 60  # 24h

def _b2s(*parts: object) -> str:
    h = blake2s(digest_size=10)
    for p in parts:
        h.update((str(p) if p is not None else "").encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()

def idem_key_fp(raw_key: str | None) -> str:
    """Short, log-safe fingerprint of the client Idempotency-Key."""
    return _b2s(raw_key or "")

def idem_redis_key(raw_key: str, service: str = "gateway", *, version: int = 2) -> str:
    """
    Stable Redis key for a client-supplied Idempotency-Key.
    Namespaced by service + version. v2 uses blake2s to align with other caches.
    """
    if version == 1:
        d = hashlib.sha1((raw_key or "").encode("utf-8")).hexdigest()[:20]
        return f"idem:v1:{service}:{d}"
    return f"idem:v2:{service}:{_b2s(raw_key or '')}"

def compute_request_scope_fp(
    *,
    method: str,
    path_or_template: str,
    query: Any,
    body: Any,
    snapshot_etag: str | None,
    policy_fp: str | None,
) -> str:
    """
    Canonical, privacy-safe scope of a request used to guard idempotent replays/merges.
    """
    # Canonicalise query with duplicate-preserving semantics (sorted k=v list).
    # Representation-invariance: treat explicit empty JSON ({}), and missing/None as equivalent.
    def _canon_qs_from_items(items) -> str:
        values_by_key = {}
        for k, v in items or []:
            ks = "" if k is None else str(k)
            vs = "" if v is None else str(v)
            values_by_key.setdefault(ks, []).append(vs)
        parts = []
        for k in sorted(values_by_key):
            for v in sorted(values_by_key[k]):
                parts.append(f"{k}={v}")
        return "&".join(parts)

    # --- query ---
    if query in (None, "", b"", bytearray(), memoryview(b"")):
        q = ""
    elif hasattr(query, "multi_items") and callable(getattr(query, "multi_items")):
        q = _canon_qs_from_items(list(query.multi_items()))
    elif isinstance(query, (list, tuple)) and query and isinstance(query[0], (list, tuple)) and len(query[0]) == 2:
        q = _canon_qs_from_items(query)
    elif isinstance(query, (bytes, bytearray, memoryview)):
        s = bytes(query).decode("utf-8", "replace").lstrip("?")
        # parse into items if it looks like a querystring; otherwise treat as JSON-like
        if "=" in s or "&" in s:
            from urllib.parse import parse_qsl  # safe local import
            q = _canon_qs_from_items(parse_qsl(s, keep_blank_values=True))
        else:
            _q = jsonx.to_jsonable(s)
            if _q is None or (isinstance(_q, dict) and not _q):
                log_stage(_IDEM_LOGGER, "idem", "empty_query_normalized", request_id=(current_request_id() or None))
                q = ""
            else:
                q = jsonx.dumps(_q, sort_keys=True)
    elif isinstance(query, str):
        s = query.lstrip("?")
        if "=" in s or "&" in s:
            from urllib.parse import parse_qsl  # safe local import
            q = _canon_qs_from_items(parse_qsl(s, keep_blank_values=True))
        else:
            _q = jsonx.to_jsonable(s)
            if _q is None or (isinstance(_q, dict) and not _q):
                log_stage(_IDEM_LOGGER, "idem", "empty_query_normalized", request_id=(current_request_id() or None))
                q = ""
            else:
                q = jsonx.dumps(_q, sort_keys=True)
    else:
        _q = jsonx.to_jsonable(query)
        if _q is None or (isinstance(_q, dict) and not _q):
            log_stage(_IDEM_LOGGER, "idem", "empty_query_normalized", request_id=(current_request_id() or None))
            _q = {}
        q = jsonx.dumps(_q, sort_keys=True)

    # Match ids.py rule: '{}' is treated as empty for query
    if q == "{}":
        log_stage(_IDEM_LOGGER, "idem", "empty_query_normalized", request_id=(current_request_id() or None))
        q = ""

    # --- body ({} ≡ None) ---
    _b = jsonx.to_jsonable(body)
    if _b is None or (isinstance(_b, dict) and not _b):
        log_stage(_IDEM_LOGGER, "idem", "empty_body_normalized", request_id=(current_request_id() or None))
        _b = {}
    b = jsonx.dumps(_b, sort_keys=True)
    return _b2s(method.upper(), path_or_template, q, b, snapshot_etag or "", policy_fp or "")

async def idem_get(rc, key: str) -> Optional[dict]:
    """
    Read the idempotency record. Returns dict or None. Works with sync/async Redis clients.
    """
    val = rc.get(key)
    val = await val if inspect.isawaitable(val) else val
    if not val:
        return None
    try:
        return jsonx.loads(val)
    except (TypeError, ValueError):
        return None

async def idem_set(rc, key: str, payload: dict, *, ttl: int = IDEM_TTL_SEC) -> None:
    """
    Overwrite the idempotency record with TTL. Works with sync/async Redis clients.
    """
    put = rc.setex(key, int(ttl), jsonx.dumps(payload, sort_keys=True))
    if inspect.isawaitable(put):
        await put

async def idem_merge(rc, key: str, patch: dict, *, expected_scope_fp: str, ttl: int = IDEM_TTL_SEC) -> bool:
    """
    Merge with guard: only apply when stored request_scope_fp matches expected.
    Returns True when merged, False on scope mismatch or missing record.
    """
    cur = await idem_get(rc, key)
    if not isinstance(cur, dict):
        return False
    if cur.get("request_scope_fp") != expected_scope_fp:
        # strategic, structured log; no PII
        log_stage(
            _IDEM_LOGGER, "idem", "idem.scope_conflict.merge",
            key=key, stored=cur.get("request_scope_fp"), expected=expected_scope_fp,
            request_id=(current_request_id() or None),
        )
        return False
    # shallow merge is fine because we pin scope; avoid cross-request leakage
    cur.update(patch or {})
    await idem_set(rc, key, cur, ttl=ttl)
    return True

# ── Tiny breadcrumb helpers (optional, convenience) ─────────────────────────
def idem_log_replay(logger, *, key_fp: str, request_id: str | None = None) -> None:
    log_stage(logger, "idem", "idem.replay", key_fp=key_fp, request_id=request_id)

def idem_log_pending(logger, *, key_fp: str, request_id: str | None = None) -> None:
    log_stage(logger, "idem", "idem.pending", key_fp=key_fp, request_id=request_id)

def idem_log_resume_seed(logger, *, key_fp: str, bundle_fp: str) -> None:
    log_stage(logger, "idem", "idem.resume_seed", key_fp=key_fp, bundle_fp=bundle_fp)

def idem_log_progress(logger, *, key_fp: str, bundle_fp: str) -> None:
    log_stage(logger, "idem", "idem.progress", key_fp=key_fp, bundle_fp=bundle_fp)

def idem_log_complete(logger, *, key_fp: str, request_id: str | None = None, mode: str = "json") -> None:
    log_stage(logger, "idem", "idem.complete", key_fp=key_fp, request_id=request_id, mode=mode)