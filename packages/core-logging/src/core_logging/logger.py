import logging, sys, time, orjson, os
from typing import Any, Dict

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
