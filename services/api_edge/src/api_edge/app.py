import asyncio
import os
import re
import time
from typing import Dict

from core_http.client import get_http_client
from core_config.constants import (
    timeout_for_stage, TIMEOUT_LLM_MS, TIMEOUT_ENRICH_MS, TIMEOUT_VALIDATE_MS
)
import httpx

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from contextlib import asynccontextmanager
from starlette.exceptions import HTTPException as StarletteHTTPException

from api_edge.rate_limit import RateLimitMiddleware
from core_config import Settings, get_settings
from core_logging import get_logger, log_stage
from core_observability.otel import setup_tracing, instrument_fastapi_app
from core_observability.otel import inject_trace_context
import core_metrics
from core_utils.health import attach_health_routes
from core_utils import idempotency_key
from core_utils.ids import generate_request_id
from core_utils import jsonx
from typing import Dict

# ---------------------------------------------------------------------------
# Application lifespan
#
# FastAPI looks up the `lifespan` argument at instantiation time.  To avoid
# NameError when referencing it, declare the function before constructing the
# FastAPI instance.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Prime Prometheus metric families before the first /metrics scrape
    and provide a single hook for future startup/shutdown tasks.
    """
    core_metrics.histogram("api_edge_ttfb_seconds", 0.0)
    core_metrics.gauge("api_edge_ttfb_seconds_latest", 0.0)
    core_metrics.counter("api_edge_fallback_total", 0)
    core_metrics.counter("api_edge_http_5xx_total", 0)
    try:
        log_stage(logger, "init", "config", upstream_base=_API_EDGE_UPSTREAM_BASE, rl_exclude_paths=list(_RL_EXCLUDE_PATHS))
    except Exception:
        pass
    yield            # ─ app is running ─
    # (optional) graceful-shutdown logic goes here

app = FastAPI(
    title="BatVault API Edge",
    version="0.2.0",
    lifespan=lifespan,
)

# Initialise tracing before wrapping the application so spans have real IDs
setup_tracing(os.getenv('OTEL_SERVICE_NAME') or 'api_edge')
# Ensure OTEL middleware wraps all subsequent middlewares/handlers
instrument_fastapi_app(app, service_name=os.getenv('OTEL_SERVICE_NAME') or 'api_edge')

def _current_trace_id() -> str | None:
    try:
        from opentelemetry import trace as _t  # type: ignore
        sp = _t.get_current_span()
        if sp:
            ctx = sp.get_span_context()  # type: ignore[attr-defined]
            if getattr(ctx, "trace_id", 0):
                return f"{ctx.trace_id:032x}"
    except Exception:
        return None
    return None

# ────────────────────────────────────────────────────────────────────────────
# 2. Settings, logging, application instance
# ────────────────────────────────────────────────────────────────────────────
settings: Settings = get_settings()
logger = get_logger("api_edge")
logger.propagate = True
# Upstream base for Gateway (env-driven)
_API_EDGE_UPSTREAM_BASE = os.getenv("API_EDGE_UPSTREAM_BASE", "http://gateway:8081").rstrip("/")
# Rate-limit exclude paths (env-driven; supports values with or without leading '/')
_RL_EXCLUDE_PATHS = tuple(
    (p if p.startswith("/") else f"/{p}")
    for p in [pp.strip() for pp in os.getenv("EDGE_RL_EXCLUDE_PATHS",
           os.getenv("API_RATE_LIMIT_EXCLUDE_PATHS", "/healthz,/readyz,/metrics")).split(",")]
    if p
)
# Edge→Gateway proxy timeout (env-driven). Default: 1.2×(LLM+ENRICH+VALIDATE) stage budgets.
_EDGE_PROXY_TIMEOUT_MS = int(os.getenv(
    "EDGE_PROXY_TIMEOUT_MS",
    str(int(1.2 * (TIMEOUT_LLM_MS + TIMEOUT_ENRICH_MS + TIMEOUT_VALIDATE_MS)))
))

# Multiplier for retry timeouts when the initial proxy call times out.  When the
# first attempt to the Gateway exceeds the configured timeout the API Edge
# performs a single retry with an extended deadline.  Introducing the
# ``EDGE_PROXY_RETRY_MULTIPLIER`` environment variable allows operators to
# increase or decrease the retry timeout proportionally.  Invalid or missing
# values fall back to a default of 1.5.
try:
    _EDGE_PROXY_RETRY_MULTIPLIER = float(os.getenv("EDGE_PROXY_RETRY_MULTIPLIER", "1.5"))
except Exception:
    _EDGE_PROXY_RETRY_MULTIPLIER = 1.5

# ────────────────────────────────────────────────────────────────────────────
# 3. Production-grade middlewares
# ────────────────────────────────────────────────────────────────────────────

# ---- 3.1 CORS --------------------------------------------------------------
_origins: list[str] = [
    o.strip()
    for o in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 3.2 Lightweight token-bucket rate-limit -------------------------------
rate_def = settings.api_rate_limit_default or "5/second"
_m = re.match(r"(?P<count>\d+)/(?P<unit>second|minute|hour)", rate_def.strip())
_count = int(_m.group("count")) if _m else 5
_seconds = {"second": 1, "minute": 60, "hour": 3600}[_m.group("unit")] if _m else 1

app.add_middleware(
    RateLimitMiddleware,
    capacity=_count,
    refill_per_sec=_count / _seconds,
    # Ops paths that must never throttle; overridable via env
    exclude_paths=_RL_EXCLUDE_PATHS,
)

# ---- 3.3 Auth stub ---------------------------------------------------------
@app.middleware("http")
async def auth_stub(request: Request, call_next):  # noqa: D401
    if settings.auth_disabled:
        request.state.auth = {"mode": "disabled"}
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        request.state.auth = {"mode": "bearer"}
        return await call_next(request)

    return JSONResponse(status_code=401, content={"error": "unauthorized"})

# ---- 3.4 Request logging & metrics -----------------------------------------
@app.middleware("http")
async def req_logger(request: Request, call_next):  # noqa: D401
    try:
        # ---- pre-request ----------------------------------------------------
        body = await request.body()
        try:
            parsed = jsonx.loads(body.decode("utf-8")) if body else None
        except Exception:
            parsed = body.decode("utf-8", errors="ignore")

        idem = idempotency_key(
            request.headers.get("Idempotency-Key"),
            request.url.path,
            dict(request.query_params),
            parsed,
        )
        request.state.request_id = idem
        # Observability: expose current trace/span IDs (should be non-zero if OTEL middleware wrapped us)
        try:
            from opentelemetry import trace as _trace  # type: ignore
            _sp = _trace.get_current_span()
            _ctx = _sp.get_span_context() if _sp else None  # type: ignore[attr-defined]
            if _ctx and getattr(_ctx, 'trace_id', 0):
                log_stage(
                    logger,
                    'observability',
                    'trace_ctx',
                    request_id=idem,
                    trace_id=f"{_ctx.trace_id:032x}",
                    span_id=f"{_ctx.span_id:016x}",
                )
        except Exception:
            pass

        t0 = time.perf_counter()
        log_stage(
            logger,
            "request",
            "request_start",
            request_id=idem,
            path=request.url.path,
            method=request.method,
        )

        # ---- downstream route ----------------------------------------------
        response = await call_next(request)

        # ---- post-request metrics & logging ---------------------------------
        dt_s = (time.perf_counter() - t0)
        core_metrics.histogram("api_edge_ttfb_seconds", dt_s)
        core_metrics.gauge("api_edge_ttfb_seconds_latest", dt_s)
        core_metrics.counter(
            "api_edge_http_requests_total",
            1,
            method=request.method,
            code=str(response.status_code),
        )
        if response.status_code >= 500:
            core_metrics.counter("api_edge_http_5xx_total", 1)

        response.headers["x-request-id"] = idem
        log_stage(
            logger,
            "request",
            "request_end",
            request_id=idem,
            status_code=response.status_code,
            latency_ms=dt_s * 1000.0,
        )
        return response

    except Exception as e:  # pragma: no cover
        if isinstance(e, StarletteHTTPException):
            log_stage(
                logger,
                "request",
                "request_error",
                error=f"{e.status_code}:{e.detail}",
            )
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

        log_stage(logger, "request", "request_error", error=str(e))
        req_id = request.headers.get("x-request-id") or generate_request_id()
        log_stage(logger, "request", "request_error", error=str(e), request_id=req_id)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL",
                    "message": "Upstream failure",
                    "details": {},
                    "request_id": req_id
                }
            }
        )

# ────────────────────────────────────────────────────────────────────────────
# 4. Metrics & health routes (prod-safe)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/metrics", include_in_schema=False)  # Prometheus scrape
def metrics() -> Response:  # pragma: no cover
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def check_gateway_ready() -> bool:
    """Returns True iff Gateway /readyz returns status: ready."""
    try:
        client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
        r = await client.get(f"{_API_EDGE_UPSTREAM_BASE}/readyz", headers=inject_trace_context({}))
        return r.status_code == 200 and r.json().get("status") == "ready"
    except Exception:
        return False


async def _readiness() -> dict:
    ready = await check_gateway_ready()
    return {"status": "ready" if ready else "degraded", "request_id": generate_request_id()}


# canonical wiring of /healthz + /readyz
attach_health_routes(
    app,
    checks={
        "liveness": lambda: {"status": "ok"},
        "readiness": _readiness,
    },
)

# ────────────────────────────────────────────────────────────────────────────
# 5. Ops / integration routes (prod-safe)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/ops/minio/bucket", include_in_schema=False)
async def ensure_bucket():
    client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
    r = await client.post(
        f"{_API_EDGE_UPSTREAM_BASE}/ops/minio/ensure-bucket",
        headers=inject_trace_context({}),
    )
    return JSONResponse(status_code=r.status_code, content=r.json())

# ----- Gateway pass-through routes (/v2 ask/query/schema) ---------------------
@app.api_route("/v2/ask", methods=["POST"])
async def proxy_v2_ask(request: Request):
    """
    Proxy /v2/ask to the Gateway while preserving streaming semantics.

    The JSON body along with selected headers (Authorization and x-request-id)
    are forwarded upstream. When a `?stream=true` parameter is present the
    upstream SSE body is relayed verbatim and audit headers (x-snapshot-etag)
    are copied into the downstream response.
    """
    return await _proxy_to_gateway(request, method="POST", path="/v2/ask")

@app.api_route("/v2/query", methods=["POST"])
async def proxy_v2_query(request: Request):
    """
    Proxy /v2/query to the Gateway while preserving streaming semantics.
    """
    return await _proxy_to_gateway(request, method="POST", path="/v2/query")

@app.get("/v2/schema/fields")
async def proxy_schema_fields(request: Request):
    """
    Proxy schema fields catalog through to the Gateway with caching.
    """
    return await _proxy_to_gateway(request, method="GET", path="/v2/schema/fields")

@app.get("/v2/schema/rels")
async def proxy_schema_rels(request: Request):
    """
    Proxy schema relations catalog through to the Gateway with caching.
    """
    return await _proxy_to_gateway(request, method="GET", path="/v2/schema/rels")

# ----- Bundle download pass-through routes (/v2/bundles) -------------------
@app.api_route("/v2/bundles/{request_id}/download", methods=["POST"])
async def proxy_bundle_download(request: Request, request_id: str):
    """Proxy POST /v2/bundles/{request_id}/download to the Gateway."""
    # Reconstruct path with path param (no query string is expected)
    return await _proxy_to_gateway(request, method="POST", path=f"/v2/bundles/{request_id}/download")


@app.get("/v2/bundles/{request_id}")
async def proxy_bundle_get(request: Request, request_id: str):
    """Proxy GET /v2/bundles/{request_id} to the Gateway."""
    return await _proxy_to_gateway(request, method="GET", path=f"/v2/bundles/{request_id}")

@app.get("/v2/bundles/{request_id}.tar")
async def proxy_bundle_tar(request: Request, request_id: str):
    """Proxy GET /v2/bundles/{request_id}.tar to the Gateway."""
    return await _proxy_to_gateway(request, method="GET", path=f"/v2/bundles/{request_id}.tar")

async def _proxy_to_gateway(request: Request, *, method: str, path: str):
    """
    Internal helper to forward API Edge requests to the Gateway.

    Builds the upstream URL from the provided path and the original query
    string, forwards the request via httpx and returns either a JSONResponse
    or a StreamingResponse. Only the `Authorization` and `x-request-id`
    headers are propagated to the upstream. When the client requests
    streaming via the `stream` query parameter the upstream SSE payload is
    streamed directly back to the caller. Upstream `x-snapshot-etag`
    headers are mirrored in the final response.
    """
    # Compose upstream URL including original query string.
    query = request.url.query
    upstream_url = f"{_API_EDGE_UPSTREAM_BASE}{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"
    # Propagate only auth and request‑id headers.
    headers: Dict[str, str] = {}
    auth_hdr = request.headers.get("authorization")
    if auth_hdr:
        headers["authorization"] = auth_hdr
    req_id = getattr(request.state, "request_id", None)
    if req_id:
        headers["x-request-id"] = req_id
    # Read body for non‑GET methods.
    body: bytes | None = None
    if method.upper() != "GET":
        body = await request.body()
    # Determine if streaming is requested.
    stream_flag = request.query_params.get("stream")
    is_stream = str(stream_flag).lower() in {"1", "true", "yes"}
    # Use a single env-driven timeout for all edge→gateway calls.
    edge_timeout_ms = _EDGE_PROXY_TIMEOUT_MS
    client = get_http_client(timeout_ms=edge_timeout_ms)
    if is_stream:
        # Streaming: forward and relay SSE (httpx 0.27: use send(..., stream=True)).
        try:
            req_up = client.build_request(
                method.upper(),
                upstream_url,
                content=body,
                headers=inject_trace_context(headers),
            )
            upstream = await client.send(req_up, stream=True)
        except Exception as e:
            # Structured signal for observability: why did the proxy fail?
            try:
                log_stage(
                    logger,
                    "request",
                    "proxy_error",
                    request_id=req_id,
                    route=path,
                    upstream=upstream_url,
                    reason=type(e).__name__,
                    timeout_ms=edge_timeout_ms,
                )
            except Exception:
                pass
            # If streaming fails, fall back to a normal POST once so callers still get a deterministic fallback.
            try:
                resp2 = await client.post(upstream_url, content=body, headers=inject_trace_context(headers))
                data2 = resp2.json()
                return JSONResponse(status_code=resp2.status_code, content=data2)
            except Exception:
                return JSONResponse(status_code=502, content={"detail": "upstream_error"})
        status_code = getattr(upstream, "status_code", 500)
        etag = upstream.headers.get("x-snapshot-etag")
        content_type = upstream.headers.get("content-type", "text/event-stream")
        x_model = upstream.headers.get("x-model")
        x_canary = upstream.headers.get("x-canary")
        # Prefer upstream request id if present, otherwise preserve local
        x_req_id = upstream.headers.get("x-request-id") or req_id
        async def _stream():
            bytes_streamed = 0
            try:
                async for chunk in upstream.aiter_bytes():
                    bytes_streamed += len(chunk)
                    yield chunk
            finally:
                try:
                    await upstream.aclose()
                except Exception:
                    pass
                try:
                    logger.info(
                        "gateway_proxy_stream",
                        extra={
                            "request_id": req_id,
                            "route": path,
                            "upstream_status": status_code,
                            "bytes_streamed": bytes_streamed,
                        },
                    )
                except Exception:
                    pass
        headers_out: Dict[str, str] = {"Cache-Control": "no-cache"}
        if etag:
            headers_out["x-snapshot-etag"] = etag
        if x_model:
            headers_out["x-model"] = x_model
        if x_canary is not None:
            headers_out["x-canary"] = x_canary
        if x_req_id:
            headers_out["x-request-id"] = x_req_id
        # expose trace id to the client (prefer upstream, fallback to local)
        try:
            headers_out["x-trace-id"] = upstream.headers.get("x-trace-id") or (_current_trace_id() or "")
        except Exception:
            pass
        return StreamingResponse(
            _stream(),
            status_code=status_code,
            media_type=content_type,
            headers=headers_out,
        )
    # Non-streaming: simple proxy with a one-shot retry on timeout.
    try:
        resp = await client.request(
            method.upper(),
            upstream_url,
            content=body,
            headers=inject_trace_context(headers),
        )
    except httpx.TimeoutException as exc:
        # Allow the gateway to finish its templater fallback path deterministically.
        try:
            log_stage(
                logger,
                "request",
                "proxy_timeout_retry",
                request_id=req_id,
                route=path,
                upstream=upstream_url,
                timeout_ms=edge_timeout_ms,
                retry_timeout_ms=int(edge_timeout_ms * _EDGE_PROXY_RETRY_MULTIPLIER),
                retries=1,
            )
            # Use the configured multiplier when constructing the retry client.
            client2 = get_http_client(timeout_ms=int(edge_timeout_ms * _EDGE_PROXY_RETRY_MULTIPLIER))
            resp = await client2.request(
                method.upper(), upstream_url, content=body, headers=inject_trace_context(headers)
            )
        except Exception as exc2:
            log_stage(
                logger,
                "request",
                "proxy_error",
                request_id=req_id,
                route=path,
                upstream=upstream_url,
                reason=f"{type(exc2).__name__}",
            )
            return JSONResponse(status_code=502, content={"detail": "upstream_error"})
    except Exception as exc:
        # Upstream failure (non-timeout).
        log_stage(
            logger,
            "request",
            "proxy_error",
            request_id=req_id,
            route=path,
            upstream=upstream_url,
            reason=f"{type(exc).__name__}",
        )
        return JSONResponse(status_code=502, content={"detail": "upstream_error"})
    finally:
        # Do not close the shared HTTP client after the request.  The client
        # instance is process‑wide and closing it here would affect other
        # concurrent requests.
        pass
    # Propagate snapshot etag and tracing headers when present.
    extra_headers: Dict[str, str] = {}
    try:
        etag2 = resp.headers.get("x-snapshot-etag")  # type: ignore[arg-type]
    except Exception:
        etag2 = None
    if etag2:
        extra_headers["x-snapshot-etag"] = etag2
    try:
        # Always include a request id for audit (prefer upstream header).
        extra_headers["x-request-id"] = resp.headers.get("x-request-id") or (req_id or "")
        # Propagate trace id for audit drawer
        try:
            extra_headers["x-trace-id"] = resp.headers.get("x-trace-id") or (_current_trace_id() or "")
        except Exception:
            pass
        # Surface model routing info when available.
        x_model2 = resp.headers.get("x-model")
        x_canary2 = resp.headers.get("x-canary")
        if x_model2:
            extra_headers["x-model"] = x_model2
        if x_canary2 is not None:
            extra_headers["x-canary"] = x_canary2
    except Exception:
        pass
    # Try to decode JSON; fallback to raw content.
    try:
        data = resp.json()
        return JSONResponse(status_code=resp.status_code, content=data, headers=extra_headers)
    except Exception:
        return Response(status_code=resp.status_code, content=resp.content, headers=extra_headers)


# ────────────────────────────────────────────────────────────────────────────
# 6. Dev / test-only helpers
# ────────────────────────────────────────────────────────────────────────────
if settings.environment in {"dev", "test"}:

    @app.get("/ratelimit-test", include_in_schema=False)
    async def _ratelimit_test() -> PlainTextResponse:  # noqa: D401
        """Zero-latency endpoint exclusively for rate-limit unit-tests."""
        return PlainTextResponse("ok")

    @app.get("/stream/demo", include_in_schema=False)
    async def stream_demo():
        """Short server-sent-events demo (5 ticks)."""
        async def _eventgen():
            for i in range(5):
                yield f"event: tick\ndata: {i}\n\n".encode()
                await asyncio.sleep(0.5)

        return StreamingResponse(_eventgen(), media_type="text/event-stream")