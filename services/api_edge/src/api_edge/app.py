from __future__ import annotations
import os
import httpx  # for precise readiness exception handling
from typing import Dict, AsyncIterator
import re
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
from core_http.errors import attach_standard_error_handlers, raise_http_error
from core_logging.error_codes import ErrorCode
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

    rid = request.headers.get("x-request-id") or generate_request_id()
    log_stage(logger, "auth", "missing_or_invalid", request_id=rid)
    raise raise_http_error(
        401,
        ErrorCode.policy_denied,
        "Unauthorized",
        rid,
    )

# ──────────────────────────────────────────────────────────────────────────────
# 3) Ops: metrics & health
# ──────────────────────────────────────────────────────────────────────────────

async def _gateway_ready() -> bool:
    """
    Returns True iff Gateway /readyz responds with {"status":"ready"}.
    """
    try:
        client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
        r = await client.get(f"{_API_EDGE_UPSTREAM_BASE}/readyz", headers=inject_trace_context({}), timeout=_EDGE_PROXY_TIMEOUT_MS/1000)
        if r.status_code != 200:
            return False
        data = r.json()  # may raise ValueError
        return data.get("status") == "ready"
    except (httpx.RequestError, ValueError):
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
    r = await client.post(
        f"{_API_EDGE_UPSTREAM_BASE}/ops/minio/ensure-bucket",
        headers=inject_trace_context({})
    )
    return JSONResponse(status_code=r.status_code, content=r.json())


# ──────────────────────────────────────────────────────────────────────────────
# 5) API pass-throughs (schema/query/config/bundles via wildcard + UI log sink)
# ──────────────────────────────────────────────────────────────────────────────

@app.api_route("/v3/query", methods=["POST"])
async def proxy_v3_query(request: Request):
    return await _proxy_to_gateway(request, method="POST", path="/v3/query")

@app.api_route("/v3/ui/logs", methods=["POST","GET","HEAD"])
async def proxy_v3_ui_logs(request: Request):
    # Accept GET/HEAD as no-ops to avoid noisy console 405s
    if request.method in ("GET","HEAD"):
        return Response(status_code=204)
    return await _proxy_to_gateway(request, method="POST", path="/v3/ui/logs")

# Back-compat for older FE builds
@app.api_route("/v2/ui/logs", methods=["POST","GET","HEAD"])
async def proxy_v2_ui_logs(request: Request):
    if request.method in ("GET","HEAD"):
        return Response(status_code=204)
    return await _proxy_to_gateway(request, method="POST", path="/v3/ui/logs")

@app.get("/v3/schema/fields")
async def proxy_schema_fields(request: Request):
    return await _proxy_to_gateway(request, method="GET", path="/v3/schema/fields")

@app.get("/v3/schema/rels")
async def proxy_schema_rels(request: Request):
    return await _proxy_to_gateway(request, method="GET", path="/v3/schema/rels")

# FE bootstrap + signing key source of truth
@app.get("/config")
async def proxy_config(request: Request):
    """
    Fetch Gateway public config, then rewrite gateway_base to the *Edge* origin
    so the frontend routes subsequent requests (bundles, receipts, downloads)
    through the Edge. This preserves observability and uniform auth/caching.
    """
    upstream_url = f"{_API_EDGE_UPSTREAM_BASE}/config"
    client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
    resp = await client.get(upstream_url, headers={"accept": "application/json"})

    # selectively pass response headers
    passthrough = {k: v for k, v in resp.headers.items() if k.lower() in _RESP_HEADER_PASS}

    # propagate non-200 upstream as-is (no broad excepts)
    if resp.status_code >= 400:
        # best-effort JSON parse; if not JSON, return text body
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return JSONResponse(status_code=resp.status_code, content=resp.json(), headers=passthrough)
        return Response(
            status_code=resp.status_code,
            content=await resp.aread(),
            media_type=content_type or "text/plain",
            headers=passthrough,
        )

    data = resp.json()  # ValueError propagates; no broad except

    # Build public base from incoming Edge request.
    # Prefer forwarded headers from the reverse proxy so we keep the *original* host:port.
    # e.g., http://localhost:5173  (strip trailing slash)
    xf_host  = request.headers.get("x-forwarded-host")
    xf_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    if xf_host:
        edge_base = f"{xf_proto}://{xf_host}".rstrip("/")
    else:
        edge_base = str(request.base_url).rstrip("/")
    # rewrite gateway_base to Edge origin
    data["gateway_base"] = edge_base
    # if memory_base is present upstream, map it to Edge as well (keeps FE joins stable)
    if isinstance(data.get("memory_base"), str):
        data["memory_base"] = f"{edge_base}/memory"

    # Normalize endpoints if an old Gateway publishes unversioned paths.
    # Guarantees FE calls /v3 even if upstream /config is stale.
    eps = (data.get("endpoints") or {})
    def _norm(p: str | None, default: str) -> str:
        p = (p or "").strip()
        return p if p.startswith("/v") else default
    eps["query"]   = _norm(eps.get("query"),   "/v3/query")
    eps["bundles"] = _norm(eps.get("bundles"), "/v3/bundles")
    data["endpoints"] = eps

    log_stage(logger, "proxy", "config_rewrite", request_id=request.headers.get("x-request-id") or "config", edge_base=edge_base)
    return JSONResponse(status_code=200, content=data, headers=passthrough)

# Passthrough for all /v3 ops paths (declare BEFORE the bundles wildcard)
@app.api_route("/v3/ops/{tail:path}", methods=["GET", "POST"])
async def proxy_ops_any(request: Request, tail: str):
    rid = request.headers.get("x-request-id")
    target = f"/v3/ops/{tail}"
    log_stage(logger, "proxy", "ops_any", request_id=(rid or "unknown"), target_path=target)
    return await _proxy_to_gateway(request, method=request.method.upper(), path=target)

# Wildcard passthrough for all /v3 bundle paths (GET/POST)
@app.api_route("/v3/bundles/{tail:path}", methods=["GET", "POST"])
async def proxy_bundles_any(request: Request, tail: str):
    rid = request.headers.get("x-request-id")
    target = f"/v3/bundles/{tail}"
    log_stage(logger, "proxy", "bundles_any", request_id=(rid or "unknown"), target_path=target)
    return await _proxy_to_gateway(request, method=request.method.upper(), path=target)

@app.get("/keys/gateway_ed25519_pub.b64")
async def edge_public_key_b64(request: Request):
    rid = request.headers.get("x-request-id") or ""
    log_stage(logger, "keys", "pubkey_b64_fetch", request_id=rid)
    client = get_http_client(timeout_ms=_EDGE_PROXY_TIMEOUT_MS)
    url = f"{_API_EDGE_UPSTREAM_BASE}/config"
    resp = await client.get(url, headers={"accept": "application/json"})
    if resp.status_code != 200:
        return Response(
            status_code=resp.status_code,
            content=await resp.aread(),
            media_type=resp.headers.get("content-type", "text/plain"),
        )
    data = resp.json()
    b64 = (data.get("signing") or {}).get("public_key_b64") or ""
    return Response(content=b64, media_type="text/plain")

# ──────────────────────────────────────────────────────────────────────────────
# 6) Internal proxy helper
# ──────────────────────────────────────────────────────────────────────────────

# Response header allowlist to preserve E2E correlation & FE cache keys
_RESP_HEADER_PASS = (
    "x-request-id",
    "x-bv-bundle-fp",
    "x-bv-policy-fingerprint",
    "x-bv-allowed-ids-fp",
    "x-bv-graph-fp",
    "x-bv-schema-fp",
    "x-snapshot-etag",
    "x-response-snapshot-etag",
    "cache-control",
    "etag",
    "content-type",
    "content-disposition",
)

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
        # Collect passthrough headers from upstream
        passthrough = {k: v for k, v in resp.headers.items() if k.lower() in _RESP_HEADER_PASS}
        if "text/event-stream" in ctype:
            async def _aiter() -> AsyncIterator[bytes]:
                async for chunk in resp.aiter_raw():
                    yield chunk
            return StreamingResponse(_aiter(), status_code=resp.status_code, media_type=ctype, headers=passthrough)

        # Otherwise buffer and return JSON/binary as appropriate
        content = await resp.aread()
        # Preserve correlation headers where useful
        rid = request.headers.get("x-request-id") or resp.headers.get("x-request-id") or generate_request_id()
        log_stage(logger, "headers", "passthrough", request_id=rid)

        # JSON?
        if "application/json" in ctype:
            data = jsonx.loads(content)
            return JSONResponse(status_code=resp.status_code, content=data, headers=passthrough)
        # Fallback: binary payload passthrough
        return Response(content=content, status_code=resp.status_code, media_type=ctype, headers=passthrough)
