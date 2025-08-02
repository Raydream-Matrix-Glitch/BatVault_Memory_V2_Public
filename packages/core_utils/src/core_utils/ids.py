import hashlib, orjson, re, unicodedata, uuid
from typing import Any, Dict, Optional

def compute_request_id(path: str, query: dict|None, body) -> str:
    q = "" if not query else orjson.dumps(query, option=orjson.OPT_SORT_KEYS).decode()
    b = "" if body is None else (body if isinstance(body, str) else orjson.dumps(body, option=orjson.OPT_SORT_KEYS).decode())
    raw = f"{path}?{q}#{b}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def idempotency_key(provided: str|None, path: str, query: dict|None, body) -> str:
    return provided or compute_request_id(path, query, body)

_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")

def slugify_id(s: str) -> str:
    """
    Canonical slug rules (spec K/L):
      - NFKC → lowercase
      - trim
      - map any non [a-z0-9] to '-'
      - collapse multiple '-' and trim '-'
    Result matches ^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$
    """
    s = unicodedata.normalize("NFKC", s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    # As a utility, we return best-effort even if too short;
    # upstream validators will enforce strict regex.
    return s

# ------------------------------------------------------------------
# Public helper: legacy alias + convenience wrapper
# ------------------------------------------------------------------

def generate_request_id(
    path: str = "",
    query: Optional[Dict[str, Any]] = None,
    body: Any | None = None,
) -> str:
    """
    • Deterministic mode – when *path* is provided, reuse
      `compute_request_id` so the ID is repeatable.
    • Fallback mode – when called with no args (common in health probes),
      return a random 16-char UUID4 fragment.
    """
    if path:
        return compute_request_id(path, query, body)
    return uuid.uuid4().hex[:16]
