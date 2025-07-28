import hashlib, orjson
from typing import Any

def canonical_json(obj: Any) -> str:
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS).decode()

def prompt_fingerprint(envelope: Any) -> str:
    canon = canonical_json(envelope)
    return hashlib.sha256(canon.encode()).hexdigest()
