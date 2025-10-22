from .logger import (
    get_logger,
    log_stage,
    log_event,
    set_snapshot_etag,
    bind_trace_ids,
    bind_request_id,
    current_request_id,
    current_trace_ids,
    log_once,
    emit_request_summary,
    emit_request_error_summary,
    record_error,
)
try:
    # Optional: trace_span is provided in logger; tolerate absence in some builds
    from .logger import trace_span  # type: ignore
except Exception:  # pragma: no cover
    trace_span = None  # type: ignore

__all__ = [
    "get_logger",
    "log_stage",
    "log_event",
    "set_snapshot_etag",
    "bind_trace_ids",
    "bind_request_id",
    "current_request_id",
    "current_trace_ids",
    "trace_span",
    "log_once",
    "emit_request_summary",
    "emit_request_error_summary",
    "record_error",
]