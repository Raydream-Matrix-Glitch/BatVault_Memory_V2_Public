import base64, hashlib, orjson, re, unicodedata, uuid
from typing import Any, Optional, Union, Dict
from core_utils.fingerprints import sha256_hex
from urllib.parse import parse_qsl
try:
    from core_logging import get_logger, log_stage, current_request_id  # type: ignore
    _IDS_LOGGER = get_logger("core_utils.ids")
except ImportError:  # pragma: no cover
    _IDS_LOGGER = None  # type: ignore

def compute_request_id(
    path: str,
    query: Optional[Union[dict, bytes, bytearray, memoryview, str, Any]],
    body: Union[bytes, bytearray, memoryview, str, Any],
) -> str:
    """
    Deterministic 16-hex request id from (path, query, body).
    Used for idempotency and replay correlation.

    Canonicalisation rules
    ----------------------
    Query:
      • Accepts dict-like, raw query string, list/tuple of pairs, or bytes.
      • Repeated keys are preserved (do **not** collapse to last value).
      • Canonical form is a **sorted** list of (key, value) pairs rendered as
        "k=v" joined by "&". Blank values are kept.
      • Raw strings beginning with "{" or "[" are parsed as JSON and re-dumped
        with sorted keys. The exact string "{}" is treated as empty.
      • Bytes are decoded as UTF-8 (replace) and then treated like strings; if
        they look like k=v query they are parsed with `keep_blank_values=True`.

    Body:
      • Accepts dict-like, string, bytes/bytearray/memoryview, or other objects.
      • JSON-like inputs (string/bytes starting with "{" or "[") are parsed and
        re-dumped using sorted keys (deterministic). The exact string "{}" is
        treated as empty.
      • Non-JSON bytes are base64-encoded and **prefixed with "b64:"** to avoid
        collisions with plain text that happens to be valid base64.
      • Other objects are serialised via `orjson.dumps(..., OPT_SORT_KEYS)`; if
        that fails (e.g. TypeError), `str(obj)` is used.

    Equivalence:
      • `{}` (empty JSON) ≡ empty string for both query and body so `None` and
        `{}` hash identically where desired.

    Notes:
      • Avoid `dict(request.query_params)`: it collapses duplicates. Pass the
        raw query string or `.multi_items()` to preserve repeats.
      • The output is stable across processes and only the **first 16 hex** of
        SHA-256 is used for readability.
    """
    # ---- query canonicalisation with multi-value support -------------------
    def _canon_qs_from_items(items) -> str:
        values_by_key: Dict[str, list[str]] = {}
        for k, v in items:
            ks = "" if k is None else str(k)
            vs = "" if v is None else str(v)
            values_by_key.setdefault(ks, []).append(vs)
        parts = []
        for k in sorted(values_by_key.keys()):
            for v in sorted(values_by_key[k]):
                parts.append(f"{k}={v}")
        return "&".join(parts)

    if query in (None, "", b"", bytearray(), memoryview(b"")):
        q = ""
    elif hasattr(query, "multi_items") and callable(getattr(query, "multi_items")):
        q = _canon_qs_from_items(list(query.multi_items()))
    elif isinstance(query, (list, tuple)) and query and isinstance(query[0], (list, tuple)) and len(query[0]) == 2:
        # Sequence of (key, value) pairs
        q = _canon_qs_from_items(query)
    elif isinstance(query, (bytes, bytearray, memoryview)):
        # Prefer parsing JSON directly from raw bytes to avoid lossy decode.
        bb = bytes(query)
        sb = bb.lstrip()
        if sb[:1] in (b"{", b"["):
            try:
                q = orjson.dumps(orjson.loads(bb), option=orjson.OPT_SORT_KEYS).decode()
            except orjson.JSONDecodeError:
                # Not valid JSON – fall back to tolerant text decode
                q = bb.decode("utf-8", "replace")
        else:
            s = bb.decode("utf-8", "replace")
            _s = s.lstrip("?")
            q = _canon_qs_from_items(parse_qsl(_s, keep_blank_values=True)) if ("=" in _s or "&" in _s) else _s
    elif isinstance(query, str):
        t = query.lstrip()
        if t[:1] in ("{", "["):
            try:
                q = orjson.dumps(orjson.loads(query), option=orjson.OPT_SORT_KEYS).decode()
            except orjson.JSONDecodeError:
                q = query
        else:
            _s = query.lstrip("?")
            q = _canon_qs_from_items(parse_qsl(_s, keep_blank_values=True)) if ("=" in _s or "&" in _s) else _s
    elif isinstance(query, dict):
        # Mapping inputs may have already collapsed duplicates; log keys for visibility.
        if _IDS_LOGGER is not None:
            log_stage(
                _IDS_LOGGER, "request_id", "query_mapping_input",
                keys=sorted(map(str, query.keys())),
                request_id=(current_request_id() or "startup"),
            )
        q = orjson.dumps(query, option=orjson.OPT_SORT_KEYS).decode()
    else:
        # Last-resort deterministic stringification
        q = str(query)

    # Representation-invariance: treat explicit empty JSON object as empty query
    if q == "{}":
        if _IDS_LOGGER is not None:
            log_stage(
                _IDS_LOGGER, "request_id", "query_empty_json_normalized",
                request_id=(current_request_id() or "startup"),
            )
        q = ""

    # Canonicalise body with representation invariance:
    # - None            -> ""
    # - str             -> if JSON-like, parse+re-dump (sorted keys); else as-is
    # - bytes/bytearray/memoryview -> if JSON-like, parse+re-dump; else base64(body)
    # - other           -> JSON dump (sorted keys)
    def _canon_from_bytes(data: Union[bytes, bytearray, memoryview]) -> str:
        bs = bytes(data)
        s = bs.lstrip()
        if s[:1] in (b"{", b"["):
            try:
                return orjson.dumps(orjson.loads(bs), option=orjson.OPT_SORT_KEYS).decode()
            except orjson.JSONDecodeError:
                # non-JSON bytes after attempt → base64 fallback
                pass
        # Non-JSON or decode error -> base64 for deterministic text keys
        if _IDS_LOGGER:
            log_stage(
                _IDS_LOGGER, "request_id", "body_base64_fallback",
                size=len(bs), reason="non_json_or_decode_error",
                request_id=(current_request_id() or "startup"),
            )
        return "b64:" + base64.b64encode(bs).decode()

    if body is None:
        b = ""
    elif isinstance(body, str):
        s = body.lstrip()
        if s[:1] in ("{", "["):
            try:
                b = orjson.dumps(orjson.loads(body), option=orjson.OPT_SORT_KEYS).decode()
            except orjson.JSONDecodeError:
                b = body
        else:
            b = body
    elif isinstance(body, (bytes, bytearray, memoryview)):
        b = _canon_from_bytes(body)
    else:
        try:
            b = orjson.dumps(body, option=orjson.OPT_SORT_KEYS).decode()
        except TypeError:
            b = str(body)

    # Representation-invariance: treat explicit empty JSON object as empty body
    if b == "{}":
        if _IDS_LOGGER is not None:
            log_stage(
                _IDS_LOGGER, "request_id", "body_empty_json_normalized",
                request_id=(current_request_id() or "startup"),
            )
        b = ""
        
    raw = f"{path}?{q}#{b}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

def idempotency_key(provided: str|None, path: str, query: dict|None, body) -> str:
    return provided or compute_request_id(path, query, body)

_TAG_OK = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")  # lower-kebab (node tags only)

def generate_request_id() -> str:
    """
    Non-deterministic 16-hex id for logging/health/exception paths.
    Kept short for log readability and parity with compute_request_id().
    """
    return uuid.uuid4().hex[:16]

def slugify_tag(s: str) -> str:
    """
    Normalise tag values to lower-kebab. Fail-closed on invalid input.
    Tags are optional and node-only (used for coarse discovery facets).
    """
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    if not _TAG_OK.match(s or ""):
        raise ValueError(f"invalid tag: {s!r}")
    return s

def stable_hex_id(value: str, length: int = 8) -> str:
    """Deterministic N-char hex id from the input value (sha1)."""
    return sha256_hex(value)[:length]

def stable_short_id(value: str) -> str:
    """Deterministic 8-char hex id from the input value (sha1)."""
    return stable_hex_id(value, length=8)