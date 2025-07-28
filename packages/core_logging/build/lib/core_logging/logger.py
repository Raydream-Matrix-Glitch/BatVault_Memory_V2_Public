import logging, sys, time, orjson, os
from typing import Any

# --------------------------------------------------------------------------- #
# Reserved names that logging.LogRecord already owns.  If we pass any of these
# in the `extra` dict, logging raises `KeyError`.  We’ll filter them out.     #
# --------------------------------------------------------------------------- #
_RESERVED: set[str] = {
    "name","msg","args","levelname","levelno",
    "pathname","filename","module","exc_info","exc_text","stack_info",
    "lineno","funcName","created","msecs","relativeCreated",
    "thread","threadName","processName","process",
}

def _default(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    raise TypeError

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(getattr(record, "created", time.time()))),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge extras
        for key, val in record.__dict__.items():
            if key in ("args","msg","levelname","levelno","pathname","filename","module",
                       "exc_info","exc_text","stack_info","lineno","funcName","created","msecs",
                       "relativeCreated","thread","threadName","processName","process"):
                continue
            base[key] = val
        return orjson.dumps(base, default=_default).decode("utf-8")

def get_logger(name: str="app", level: str|None=None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(level or os.getenv("SERVICE_LOG_LEVEL","INFO"))
    return logger

def log_event(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    logger.info(event, extra=kwargs)

def log_stage(
    logger: logging.Logger,
    stage: str,
    event: str,
    *,
    request_id: str | None = None,
    prompt_fingerprint: str | None = None,
    snapshot_etag: str | None = None,
    **kwargs: Any,
) -> None:
    extras = {"stage": stage}
    if request_id:
        extras["request_id"] = request_id
    if prompt_fingerprint:
        extras["prompt_fingerprint"] = prompt_fingerprint
    if snapshot_etag:
        extras["snapshot_etag"] = snapshot_etag
    extras.update(kwargs)
    # Strip keys that would collide with LogRecord attributes
    safe_extras = {k: v for k, v in extras.items() if k not in _RESERVED}
    logger.info(event, extra=safe_extras)
