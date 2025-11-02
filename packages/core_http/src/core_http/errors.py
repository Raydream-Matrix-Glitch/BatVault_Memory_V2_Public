from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from core_logging import get_logger, log_stage
from core_utils.ids import compute_request_id, generate_request_id
from core_utils import jsonx
from core_logging.error_codes import ErrorCode  # reuse codes; do not duplicate
from fastapi import HTTPException as FastAPIHTTPException

def raise_http_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    *,
    details: object | None = None,
) -> FastAPIHTTPException:
    """
    Construct a FastAPI HTTPException with the canonical error envelope.
    attach_standard_error_handlers() will pass this JSON through unchanged.
    """
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        },
        "request_id": request_id,
    }
    if details is not None:
        payload["error"]["details"] = jsonx.sanitize(details)
    return FastAPIHTTPException(status_code=status_code, detail=payload)

def attach_standard_error_handlers(app: FastAPI, *, service: str) -> None:
    """
    Uniform error shaping across services:
      - 422: Pydantic validation
      - Starlette HTTP errors (JSON passthrough)
      - 500: Catch-all with {code, message, details, request_id}
    """
    logger = get_logger(service)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        try:
            body = await request.body()
        except (RuntimeError, ValueError, TypeError):
            body = b""
        # Representation-invariance: treat explicit empty JSON object as empty
        _b = body.lstrip()
        if _b[:1] == b"{":
            try:
                import orjson as _orjson  # local import; JSON-only try/except
                if _orjson.loads(body) == {}:
                    log_stage(get_logger(service), "request_id", "body_empty_json_normalized",
                              url=str(request.url), method=request.method)
                    body = b""
            except _orjson.JSONDecodeError:
                pass
        try:
            req_id = compute_request_id(str(request.url.path), request.url.query, body)
        except (TypeError, ValueError):
            req_id = generate_request_id()
        try:
            log_stage(logger, "validation", "failed",
                      request_id=req_id, errors=jsonx.sanitize(exc.errors()),
                      url=str(request.url), method=request.method)
        except (RuntimeError, ValueError, TypeError):
            pass
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.validation_failed,
                    "message": "Request validation failed",
                    "details": {"errors": jsonx.sanitize(exc.errors())},
                    "request_id": req_id,
                },
                "request_id": req_id,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(_: Request, exc: StarletteHTTPException):
        # Keep Starlette semantics but JSON-first body
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(request: Request, exc: Exception):
        try:
            body = await request.body()
        except (RuntimeError, ValueError, TypeError):
            body = b""
        # Representation-invariance: treat explicit empty JSON object as empty
        _b = body.lstrip()
        if _b[:1] == b"{":
            try:
                import orjson as _orjson
                if _orjson.loads(body) == {}:
                    log_stage(logger, "request_id", "body_empty_json_normalized",
                              url=str(request.url), method=request.method)
                    body = b""
            except _orjson.JSONDecodeError:
                pass
        try:
            req_id = compute_request_id(str(request.url.path), request.url.query, body)
        except (TypeError, ValueError):
            req_id = generate_request_id()
        try:
            log_stage(logger, "request", "unhandled_exception",
                      request_id=req_id, error=str(exc), error_type=exc.__class__.__name__)
        except (RuntimeError, ValueError, TypeError):
            pass
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.internal,
                    "message": "Unexpected error",
                    "details": jsonx.sanitize({"type": exc.__class__.__name__, "message": str(exc)}),
                    "request_id": req_id,
                },
                "request_id": req_id,
            },
        )