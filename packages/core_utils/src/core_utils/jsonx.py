from __future__ import annotations
from typing import Any, Mapping
import json as _pyjson
import os

# Optional, fast path JSON
try:
    import orjson as _orjson  # type: ignore
except ImportError:  # pragma: no cover - orjson missing/unavailable
    _orjson = None

# Optional override to force stdlib json (diagnostics/compat)
if os.getenv("JSONX_FORCE_STDJSON", "").strip().lower() in ("1","true","yes"):  # pragma: no cover
    _orjson = None

# Optional Pydantic import across v1/v2
try:
    from pydantic import BaseModel  # type: ignore
except ImportError:  # pragma: no cover
    BaseModel = object  # type: ignore

__all__ = ["dumps", "loads", "sanitize"]

def _is_pydantic_model(obj: Any) -> bool:
    # Works for both pydantic v1/v2 and duck-typed models
    return isinstance(obj, BaseModel) or hasattr(obj, "model_dump") or hasattr(obj, "dict")

def sanitize(obj: Any) -> Any:
    """Recursively convert *obj* into something JSON-serialisable.

    - Exceptions → {"error": <Type>, "message": str(e)}
    - Pydantic models → model_dump(mode="python")
    - bytes → UTF-8 string (replacement on errors)
    - sets/tuples → lists
    - objects with __dict__ → str(obj)
    """
    # Fast-path primitives
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Exceptions
    if isinstance(obj, BaseException):
        return {"error": obj.__class__.__name__, "message": str(obj)}

    # Pydantic models
    if _is_pydantic_model(obj):  # pragma: no cover - defensive
        # Prefer v2 API, fall back to v1
        try:
            return obj.model_dump(mode="python")  # type: ignore[attr-defined]
        except (AttributeError, TypeError, ValueError):
            try:
                return obj.dict()  # type: ignore[attr-defined]
            except (AttributeError, TypeError, ValueError):
                return str(obj)

    # Bytes → text
    if isinstance(obj, (bytes, bytearray, memoryview)):
        # 'replace' guarantees decoding won't raise
        return (obj.decode("utf-8", "replace")
                if isinstance(obj, (bytes, bytearray))
                else bytes(obj).decode("utf-8", "replace"))

    # Collections
    if isinstance(obj, Mapping):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in obj]

    # Fallbacks for common types
    for attr in ("isoformat",):  # datetimes, dates, etc.
        if hasattr(obj, attr):
            try:
                return getattr(obj, attr)()
            except (TypeError, ValueError):
                pass

    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)
    
def to_jsonable(obj: Any) -> Any:
    """
    Canonical JSON-friendly projection used by idempotency and cache keys.
    Intentional thin alias of `sanitize` for backwards-compatible call-sites.
    """
    return sanitize(obj)

__all__.append("to_jsonable")

def dumps(obj: Any) -> str:
    """
    Fast JSON dump that returns a *str* (UTF‑8) and ensures deterministic key ordering.

    Canonicalising the JSON output is important for hashing, caching, and
    fingerprinting across services. By always sorting keys, downstream
    components such as the audit trail and caching layers can rely on a
    stable representation independent of Python dictionary ordering.

    Args:
        obj: A JSON‑serialisable Python object.
    Returns:
        A UTF‑8 encoded JSON string with sorted keys.
    """
    def _default(o: Any) -> Any:
        return sanitize(o)
    # Fast path: orjson (if available)
    if _orjson is not None:
        try:
            return _orjson.dumps(obj, option=_orjson.OPT_SORT_KEYS, default=_default).decode("utf-8")
        except Exception:
            # fall through to stdlib
            pass
    # Robust fallback: stdlib json (handles more exotic types via default())
    return _pyjson.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        default=_default,
        separators=(",", ":"),  # canonical/compact
    )

def loads(data: str | bytes) -> Any:
    """Robust JSON load from str/bytes with BOM/encoding fallback.

    - Accepts str or bytes
    - Tries orjson first (bytes only)
    - On failure, strips UTF-8 BOM and retries once
    - Falls back to stdlib json with 'utf-8-sig' decoding
    """
    # Normalise to bytes for fast path
    if isinstance(data, str):
        b = data.encode("utf-8", errors="strict")
    else:
        b = data

    # Fast path: orjson if available
    if _orjson is not None:
        try:
            return _orjson.loads(b)
        except Exception:
            # Try stripping BOM once then retry
            try:
                if len(b) >= 3 and b[:3] == b"\xef\xbb\xbf":
                    return _orjson.loads(b[3:])
            except Exception:
                pass
            # fall through to stdlib

    # Fallback: stdlib json with utf-8-sig to drop BOM if present
    try:
        txt = b.decode("utf-8-sig")
    except Exception:
        txt = b.decode("utf-8", errors="replace")
    return _pyjson.loads(txt)