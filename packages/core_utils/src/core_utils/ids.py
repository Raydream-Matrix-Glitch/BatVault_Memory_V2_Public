import hashlib, orjson
from typing import Any

def compute_request_id(path: str, query: dict[str,Any]|None, body: Any) -> str:
    q = "" if not query else orjson.dumps(query, option=orjson.OPT_SORT_KEYS).decode()
    b = "" if body is None else (body if isinstance(body, str) else orjson.dumps(body, option=orjson.OPT_SORT_KEYS).decode())
    raw = f"{path}?{q}#{b}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def idempotency_key(provided: str|None, path: str, query: dict[str,Any]|None, body: Any) -> str:
    return provided or compute_request_id(path, query, body)
