from __future__ import annotations
from typing import Any
import orjson
__all__ = ["dumps", "loads"]
def dumps(obj: Any) -> str:
    """Fast JSON dump that returns *str* (UTF-8)."""
    return orjson.dumps(obj).decode("utf-8")
def loads(data: str | bytes) -> Any:
    """Fast JSON load from str/bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return orjson.loads(data)