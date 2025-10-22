from __future__ import annotations
import os
import re
import httpx  # for precise readiness exception handling
from typing import Dict, AsyncIterator
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from core_config import Settings, get_settings
from core_config.constants import (
    TIMEOUT_LLM_MS,
    TIMEOUT_ENRICH_MS,
    TIMEOUT_VALIDATE_MS,
)
from core_http.client import get_http_client
from core_logging import get_logger, log_stage
from core_metrics import counter as metric_counter, histogram as metric_histogram  # optional local use
from core_utils.fastapi_bootstrap import setup_service
from core_observability.otel import inject_trace_context
from core_http.errors import attach_standard_error_handlers
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_utils import jsonx

app = FastAPI(
    title="BatVault API Edge",
    version="0.2.0",
)
setup_service(app, 'api_edge')
attach_standard_error_handlers(app, service="api_edge")

async def _edge_readiness() -> dict:
    ok = await _gateway_ready()
    return {'status': 'ready' if ok else 'degraded', 'ready': bool(ok), 'request_id': generate_request_id()}

attach_health_routes(app, checks={'liveness': (lambda: True), 'readiness': _edge_readiness})

# ──────────────────────────────────────────────────────────────────────────────
# 1) Application setup & lifespan
# ──────────────────────────────────────────────────────────────────────────────

logger = get_logger("api_edge")
settings: Settings = get_settings()

_API_EDGE_UPSTREAM_BASE = os.getenv("API_EDGE_UPSTREAM_BASE", "http://gateway:8081").rstrip("/")

# Edge→Gateway proxy timeout (env-driven). Default: 1.2×(LLM+ENRICH+VALIDATE) budgets.
_EDGE_PROXY_TIMEOUT_MS = int(
    os.getenv(
        "EDGE_PROXY_TIMEOUT_MS",
        str(int(1.2 * (TIMEOUT_LLM_MS + TIMEOUT_ENRICH_MS + TIMEOUT_VALIDATE_MS))),
    )
)

# Retry deadline multiplier for a single follow-up attempt on upstream timeout
try:
    _EDGE_PROXY_RETRY_MULTIPLIER = float(os.getenv("EDGE_PROXY_RETRY_MULTIPLIER", "1.5"))
except Exception:
    _EDGE_PROXY_RETRY_MULTIPLIER = 1.5

# Rate-limit exclude paths (supports values with or without leading '/')
_RL_EXCLUDE_PATHS = tuple(
    (p if p.startswith("/") else f"/{p}")
    for p in [pp.strip() for pp in os.getenv("EDGE_RL_EXCLUDE_PATHS", "/healthz,/readyz,/metrics").split(",")]
    if p
)

@app.on_event("startup")
async def _startup() -> None:
    # Prime metric families (cheap, avoids empty families on first scrape)
    metric_histogram("api_edge_ttfb_seconds", 0.0)
    metric_counter("api_edge_http_5xx_total", 0)
    metric_counter("api_edge_http_requests_total", 0, method="GET", code="200")
    log_stage(
        logger, "init", "config",
        upstream_base=_API_EDGE_UPSTREAM_BASE,
        rl_exclude_paths=list(_RL_EXCLUDE_PATHS),
        request_id="startup",
    )

# ──────────────────────────────────────────────────────────────────────────────
# 2) Middlewares: Auth (CORS & rate-limit handled by setup_service via env)
# ──────────────────────────────────────────────────────────────────────────────

# Minimal auth stub (kept intentionally – required for public/stub deployments)
@app.middleware("http")
async def auth_stub(request: Request, call_next):
    if settings.auth_disabled:
        request.state.auth = {"mode": "disabled"}
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        request.state.auth = {"mode": "bearer"}
        return await call_next(request)

    return JSONResponse(status_code=401, content={"error": "unauthorized"})

# ──────────────────────────────────────────────────────────────────────────────
# 3) Ops: metrics & health
# ──────────────────────────────────────────────────────────────────────────────

async def _gateway_ready() -> bool:
    """
    Returns True iff Gateway /readyz responds with {"status":"ready"}.
    """
    try:
        client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
        r = await client.get(f"{_API_EDGE_UPSTREAM_BASE}/readyz", headers=inject_trace_context({}))
        return r.status_code == 200 and (r.json().get("status") == "ready")
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError, RuntimeError):
        return False


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    # Process is up; detailed checks belong in readiness
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz() -> dict:
    ok = await _gateway_ready()
    return {
        "status": "ready" if ok else "degraded",
        "ready": bool(ok),
        "request_id": generate_request_id(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 4) Ops passthrough
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/ops/minio/bucket", include_in_schema=False)
async def ensure_bucket():
    """
    Pass-through to Gateway's /ops/minio/ensure-bucket (POST).
    """
    client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
    r = await client.post(f"{_API_EDGE_UPSTREAM_BASE}/ops/minio/ensure-bucket", headers=inject_trace_context({}))
    return JSONResponse(status_code=r.status_code, content=r.json())


# ──────────────────────────────────────────────────────────────────────────────
# 5) API v2 pass-throughs (schema/query/bundles)
# ──────────────────────────────────────────────────────────────────────────────

@app.api_route("/v2/query", methods=["POST"])
async def proxy_v2_query(request: Request):
    return await _proxy_to_gateway(request, method="POST", path="/v2/query")


@app.get("/v2/schema/fields")
async def proxy_schema_fields(request: Request):
    return await _proxy_to_gateway(request, method="GET", path="/v2/schema/fields")


@app.get("/v2/schema/rels")
async def proxy_schema_rels(request: Request):
    return await _proxy_to_gateway(request, method="GET", path="/v2/schema/rels")


@app.api_route("/v2/bundles/{request_id}/download", methods=["POST"])
async def proxy_bundle_download(request: Request, request_id: str):
    return await _proxy_to_gateway(request, method="POST", path=f"/v2/bundles/{request_id}/download")


@app.get("/v2/bundles/{request_id}")
async def proxy_bundle_get(request: Request, request_id: str):
    return await _proxy_to_gateway(request, method="GET", path=f"/v2/bundles/{request_id}")


@app.get("/v2/bundles/{request_id}.tar")
async def proxy_bundle_tar(request: Request, request_id: str):
    return await _proxy_to_gateway(request, method="GET", path=f"/v2/bundles/{request_id}.tar")


# ──────────────────────────────────────────────────────────────────────────────
# 6) Internal proxy helper
# ──────────────────────────────────────────────────────────────────────────────

async def _proxy_to_gateway(request: Request, *, method: str, path: str):
    """
    Forward the request to the Gateway, preserving JSON bodies and streaming
    responses when applicable. Mirrors selected headers and x-snapshot-etag.
    """
    # Compose upstream URL with original query string
    query = request.url.query
    upstream_url = f"{_API_EDGE_UPSTREAM_BASE}{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    # Propagate identity/policy headers (JSON-first contract)
    headers: Dict[str, str] = {}
    forward_headers = [
        "authorization",
        "x-request-id", "x-trace-id",
        "x-user-id", "x-user-roles", "x-user-namespaces",
        "x-policy-version", "x-policy-key",
        "x-domain-scopes", "x-edge-allow", "x-max-hops", "x-sensitivity-ceiling",
    ]
    for h in forward_headers:
        v = request.headers.get(h)
        if v is not None:
            headers[h] = v

    client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)

    # Prepare body for methods that can carry one
    body = await request.body() if method in ("POST", "PUT", "PATCH") else None

    # Try streaming first; fallback to buffered JSON
    async with client.stream(method, upstream_url, headers=inject_trace_context(headers), content=body) as resp:  # type: ignore[attr-defined]
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            async def _aiter() -> AsyncIterator[bytes]:
                async for chunk in resp.aiter_raw():
                    yield chunk
            return StreamingResponse(_aiter(), status_code=resp.status_code, media_type=ctype)

        # Otherwise buffer and return JSON/binary as appropriate
        content = await resp.aread()
        # v3: no header mirroring; pass-through only (envelope is source of truth)
        extra_headers = {}
        # Correlate edge proxy headers behavior with incoming request id
        rid = request.headers.get("x-request-id") or generate_request_id()
        log_stage(logger, "headers", "passthrough_only", request_id=rid)

        # JSON?
        if "application/json" in ctype:
            data = jsonx.loads(content)
            return JSONResponse(status_code=resp.status_code, content=data, headers=extra_headers)
        # Fallback: binary payload passthrough
        return Response(content=content, status_code=resp.status_code, media_type=ctype, headers=extra_headers)
