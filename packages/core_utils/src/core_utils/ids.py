import hashlib, orjson, re, unicodedata, uuid
from typing import Any, Dict, Optional
import hashlib as _hashlib

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
# Tag slugging helper
# ------------------------------------------------------------------

def slugify_tag(s: str) -> str:
    """
    Canonical slug rules for tags.  Tags differ from general identifiers in
    that they use underscores (``_``) as the canonical separator instead of
    hyphens.  This helper lower‑cases the input, normalises it using
    Unicode NFKC, collapses any sequence of non‑alphanumeric characters
    into a single underscore and trims leading/trailing underscores.  It
    mirrors the behaviour of :func:`core_validator._slugify_to_underscores`.

    Parameters
    ----------
    s: str
        The tag string to normalise.

    Returns
    -------
    str
        A lower‑cased slug string using underscores.
    """
    import unicodedata
    # Normalise to Unicode NFKC and lowercase
    s_norm = unicodedata.normalize("NFKC", s or "").strip().lower()
    # Replace any sequence of non‑[a‑z0‑9] characters with an underscore
    s_norm = re.sub(r"[^a-z0-9]+", "_", s_norm)
    # Trim leading/trailing underscores
    return s_norm.strip("_")


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

def is_slug(s: str) -> bool:
    """
    Return True if *s* is already a canonical slug (spec §B-2).
    Forward-compatible with Milestone 4 by centralizing slug semantics.
    """
    if not s:
        return False
    return bool(_SLUG_OK.match(s.strip()))

_TAG_RE = re.compile(r"[^a-z0-9]+")

def slugify_tag(s: str) -> str:
    """
    Canonical tag slug:
      - NFKC normalize
      - lowercase
      - replace any non [a-z0-9] sequence with a single underscore
      - trim leading/trailing underscores
      - collapse multiple underscores
    Deterministic and ASCII-safe; aligns all services on tag shape.
    """
    if s is None:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = _TAG_RE.sub("_", s).strip("_")
    # collapse multiple underscores
    s = re.sub(r"_+", "_", s)
    return s

def stable_short_id(value: str) -> str:
    """
    Deterministic 8-char hex id from the input value (sha1).
    Handy for correlating batches/runs in logs without leaking details.
    """
    if value is None:
        value = ""
    return _hashlib.sha1(str(value).encode()).hexdigest()[:8]