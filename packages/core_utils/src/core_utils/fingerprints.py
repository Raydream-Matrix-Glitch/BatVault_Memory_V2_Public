# packages/core_utils/src/core_utils/fingerprints.py

import hashlib
import orjson
from typing import Any

# ensure fully stable encoding: sort keys + drop microseconds
_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_OMIT_MICROSECONDS

def canonical_json(obj: Any) -> bytes:
    """
    Serialize `obj` to canonical JSON bytes:
    - keys sorted
    - no microseconds in timestamps
    - compact representation
    """
    return orjson.dumps(obj, option=_OPTS)

def prompt_fingerprint(envelope: Any) -> str:
    """
    Compute the SHA-256 fingerprint of the given envelope by
    hashing its canonical JSON representation.
    """
    canon_bytes = canonical_json(envelope)
    h = hashlib.sha256(canon_bytes).hexdigest()
    return f"sha256:{h}"


def parse_fingerprint(value: str) -> tuple[str, str]:
    """Parse a fingerprint string, returning (algorithm, hexval).
    Accepts both "sha256:<hex>" and bare hex for backward compatibility.
    Always returns ("sha256", <hex>).
    """
    if isinstance(value, str) and value.startswith("sha256:"):
        return ("sha256", value.split(":", 1)[1])
    return ("sha256", value)
