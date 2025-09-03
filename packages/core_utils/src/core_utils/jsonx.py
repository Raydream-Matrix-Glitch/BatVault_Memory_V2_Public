from typing import Any, Mapping, Iterable
import orjson
from pydantic import BaseModel  # type: ignore

__all__ = ["dumps", "loads", "sanitize"]

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
    if isinstance(obj, BaseModel):  # pragma: no cover - defensive
        try:
            return obj.model_dump(mode="python")
        except Exception:
            return str(obj)

    # Bytes → text
    if isinstance(obj, (bytes, bytearray, memoryview)):
        try:
            return (obj.decode("utf-8", "replace") if isinstance(obj, (bytes, bytearray)) else bytes(obj).decode("utf-8", "replace"))
        except Exception:
            return str(obj)

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
            except Exception:
                pass

    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)
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
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS, default=_default).decode("utf-8")

def loads(data: str | bytes) -> Any:
    """Fast JSON load from str/bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return orjson.loads(data)