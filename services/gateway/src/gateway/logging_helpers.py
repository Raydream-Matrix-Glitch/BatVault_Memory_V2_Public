from __future__ import annotations
from typing import Any
from core_logging import log_stage as _log_stage, trace_span as _trace_span, get_logger

_logger = get_logger("gateway")

def stage(stage_name: str, action: str, **fields: Any) -> None:
    """
    Thin wrapper around core_logging.log_stage that guarantees a consistent envelope.
    Always injects service='gateway' and passes through common IDs when present.
    """
    try:
        _log_stage(_logger, stage_name, action, service="gateway", **fields)
    except Exception:
        # Never let logging break the hot path
        pass

def span(name: str, **attrs: Any):
    """Return a context manager for a tracing span."""
    return _trace_span(name, **attrs)