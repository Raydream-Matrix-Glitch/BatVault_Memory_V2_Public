import hashlib
import orjson
import os
import re
from pathlib import Path
from typing import Any, Iterable, Dict, List, Tuple, Union

# ensure fully stable encoding: sort keys + drop microseconds
_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_OMIT_MICROSECONDS

# Public API of this module
__all__ = [
    "canonical_json",
    "sha256_hex",
    "ensure_sha256_prefix",
    "parse_fingerprint",
    "normalize_fingerprint",
    "prompt_fingerprint",
    "graph_fp",
    "allowed_ids_fp",
    "schema_dir_fp",
]

# ── Canonical JSON ─────────────────────────────────────────────────────────
def canonical_json(obj: Any) -> bytes:
    """
    Serialize `obj` to canonical JSON bytes:
    - keys sorted
    - no microseconds in timestamps
    - compact representation
    """
    return orjson.dumps(obj, option=_OPTS)

# ── Core helpers (hashing & parsing) ─────────────────────────────────────────
def sha256_hex(data: Union[str, bytes, bytearray, memoryview]) -> str:
    """Return hex SHA-256 digest. Accepts str and bytes-like; strings are UTF-8 encoded."""
    if isinstance(data, str):
        b = data.encode("utf-8")
    elif isinstance(data, (bytearray, memoryview)):
        b = bytes(data)
    elif isinstance(data, bytes):
        b = data
    else:
        raise TypeError(f"sha256_hex expects str or bytes-like, got {type(data).__name__}")
    return hashlib.sha256(b).hexdigest()

def ensure_sha256_prefix(value: str) -> str:
    """Ensure the fingerprint string has the ``sha256:`` prefix without recomputation."""
    if isinstance(value, str) and value.startswith("sha256:"):
        return value
    return "sha256:" + value

def parse_fingerprint(value: str) -> tuple[str, str]:
    """Parse a fingerprint string, returning (algorithm, hexval).
    Accepts both "sha256:<hex>" and bare hex for backward compatibility.
    Always returns ("sha256", <hex>).
    """
    if isinstance(value, str) and value.startswith("sha256:"):
        return ("sha256", value.split(":", 1)[1])
    return ("sha256", value)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX128_RE = re.compile(r"^[0-9a-f]{128}$")

def normalize_fingerprint(value: str) -> str:
    """
    Normalize to canonical ``sha256:<64-hex>``.
    Accepts:
      - "sha256:<64-hex>"
      - bare 64-hex
      - double-hex (hex-of-ASCII of a 64-hex)
    Returns "sha256:unknown" for anything else.
    """
    algo, hexval = parse_fingerprint(value or "")
    if _HEX128_RE.fullmatch(hexval):
        try:
            decoded = bytes.fromhex(hexval).decode("ascii")
            if _HEX64_RE.fullmatch(decoded):
                hexval = decoded
        except Exception:
            # leave as-is; will fail the 64-hex check below
            pass
    if not _HEX64_RE.fullmatch(hexval):
        return "sha256:unknown"
    return f"{algo}:{hexval}"

def prompt_fingerprint(envelope: Any) -> str:
    """Deterministic SHA-256 fingerprint over the canonical JSON of `envelope`."""
    digest = sha256_hex(canonical_json(envelope))
    return ensure_sha256_prefix(digest)

def schema_dir_fp(dirpath: str | os.PathLike[str]) -> str:
    """
    Compute a stable fingerprint over all JSON Schemas in a directory tree.
    Walks *.json files under `dirpath`, sorts by relative path, hashes canonical content,
    then hashes the ordered list of (path, file_sha256).
    Returns ``sha256:<hex>``.
    """
    root = Path(dirpath)
    items: list[tuple[str,str]] = []
    for p in sorted(root.rglob("*.json"), key=lambda p: p.as_posix()):
        data = p.read_bytes()
        try:
            # canonicalize JSON content before hashing (robust against whitespace churn)
            obj = orjson.loads(data)
            blob = canonical_json(obj)
        except Exception:
            # non-JSON blobs (shouldn't happen in schemas) fall back to raw bytes
            blob = data
        items.append((p.relative_to(root).as_posix(), sha256_hex(blob).split("sha256:",1)[-1]))
    payload = {"schemas": items}
    return ensure_sha256_prefix(sha256_hex(canonical_json(payload)))

# ── Deterministic graph / ids fingerprints (PR-2) ────────────────────────
def _edge_sort_key(edge: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """Stable sort key for an edge using (type, from, to, timestamp).
    Missing fields are treated as empty strings for deterministic ordering.
    """
    et = str(edge.get("type", ""))
    ef = str(edge.get("from", ""))
    eo = str(edge.get("to", ""))
    ts = str(edge.get("timestamp", ""))
    return (et, ef, eo, ts)

def graph_fp(anchor: Any, edges: Iterable[Dict[str, Any]]) -> str:
    """Compute a deterministic fingerprint for a small graph view.

    Hash is over canonical JSON of:
        {"anchor": <anchor>, "edges": <edges_sorted>}
    with edges sorted by (type, from, to, timestamp).
    Returns a ``sha256:<hex>`` string.
    """
    edges_list: List[Dict[str, Any]] = list(edges or [])
    edges_sorted = sorted(edges_list, key=_edge_sort_key)
    payload = {"anchor": anchor, "edges": edges_sorted}
    digest = sha256_hex(canonical_json(payload))
    return ensure_sha256_prefix(digest)

def allowed_ids_fp(ids: Iterable[str]) -> str:
    """Deterministic fingerprint for a set/list of allowed IDs.

    IDs are stringified and sorted lexicographically before hashing the
    canonical JSON payload {"ids": [..sorted..]}. Returns ``sha256:<hex>``.
    """
    ids_sorted = sorted([str(i) for i in (ids or [])])
    payload = {"ids": ids_sorted}
    digest = sha256_hex(canonical_json(payload))
    return ensure_sha256_prefix(digest)
