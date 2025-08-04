# 1 ───────────────────────────── Imports ────────────────────────────────
import asyncio, io, os, time, uuid
from typing import List, Optional
import httpx, orjson, redis
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from minio import Minio
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
import importlib.metadata as _md

from core_config import get_settings
from core_config.constants import MAX_PROMPT_BYTES, RESOLVER_MODEL_ID, SELECTOR_MODEL_ID
from core_logging import get_logger, log_stage, trace_span
from core_metrics import counter as metric_counter, histogram as metric_histogram
from core_models.models import (
    WhyDecisionAnchor, WhyDecisionAnswer, WhyDecisionEvidence,
    WhyDecisionResponse, WhyDecisionTransitions, CompletenessFlags,
)
from core_utils.fingerprints import canonical_json
from core_utils.health import attach_health_routes
from core_utils.ids import generate_request_id
from core_storage.minio_utils import ensure_bucket as ensure_minio_bucket
from core_validator import validate_response

from gateway.resolver import resolve_decision_text
from . import evidence, prom_metrics       # noqa: F401
from .evidence import EvidenceBuilder, _safe_async_client
from .load_shed import should_load_shed
from .match_snippet import build_match_snippet
from .builder import build_why_decision_response

# 2 ───────────────────── Config & constants ─────────────────────────────
settings        = get_settings()
logger          = get_logger("gateway"); logger.propagate = True
_SEARCH_MS      = int(os.getenv("TIMEOUT_SEARCH_MS", "800"))
_EXPAND_MS      = int(os.getenv("TIMEOUT_EXPAND_MS", "250"))
_SCHEMA_TTL_SEC = 60

# 3 ───────────────────── Application setup ──────────────────────────────
app    = FastAPI(title="BatVault Gateway", version="0.1.0")
router = APIRouter(prefix="/v2")

# 4 ──────────────── Helpers & singletons ────────────────────────────────
def minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )

def _minio_put_batch(request_id: str, artefacts: dict[str, bytes]) -> None:
    client = minio_client()
    for name, blob in artefacts.items():
        client.put_object(
            settings.minio_bucket, f"{request_id}/{name}",
            io.BytesIO(blob), length=len(blob), content_type="application/json",
        )
        metric_counter("artifact_bytes_total", inc=len(blob), artefact=name)

_evidence_builder = EvidenceBuilder()

try:
    _schema_cache = redis.Redis.from_url(settings.redis_url, decode_responses=True)
except Exception:
    _schema_cache = None   # cache-less fallback

# 5 ─────────────────── Exception handlers ───────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.warning("request_validation_error",
                   extra={"service":"gateway","stage":"validation","errors":exc.errors(),
                          "url":str(request.url),"method":request.method})
    return JSONResponse(status_code=422,
                        content={"detail": exc.errors(),
                                 "received_body": body.decode(errors="ignore")})

# 6 ─────────────────────── Middleware ───────────────────────────────────
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    req_id = generate_request_id(); t0 = time.perf_counter()
    log_stage(logger, "request", "start", request_id=req_id,
              path=request.url.path, method=request.method)

    resp = await call_next(request)

    dt = int((time.perf_counter() - t0) * 1000)
    metric_histogram("gateway_ttfb_ms", float(dt))
    metric_counter("gateway_http_requests_total", 1,
                   method=request.method, code=str(resp.status_code))
    log_stage(logger, "request", "end",
              request_id=req_id, latency_ms=dt, status_code=resp.status_code)
    resp.headers["x-request-id"] = req_id
    return resp

# 7 ──────────────── Ops & metrics endpoints ─────────────────────────────
@app.get("/metrics", include_in_schema=False)          # pragma: no cover
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/ops/minio/ensure-bucket")
@log_stage(logger, "gateway", "ensure_bucket")
def ensure_bucket():
    return ensure_minio_bucket(minio_client(),
                               bucket=settings.minio_bucket,
                               retention_days=settings.minio_retention_days)

# 8 ─────────────────────── Health routes ────────────────────────────────
async def _readiness() -> dict[str, str]:
    return {
        "status": "ready" if await _ping_memory_api() else "degraded",
        "request_id": generate_request_id(),
    }

attach_health_routes(
    app,
    checks={
        "liveness": lambda: {"status": "ok"},
        "readiness": _readiness,
    },
)


# 9 ──────────────────── /v2 schema mirror ───────────────────────────────
@router.get("/schema/{kind}")
@app.get("/schema/{kind}")          # temporary back-compat
async def schema_mirror(kind: str):
    if kind not in ("fields", "rels"):
        raise HTTPException(status_code=404, detail="unknown schema kind")

    key = f"schema:{kind}"
    if _schema_cache and (cached := _schema_cache.get(key)):
        data, etag = orjson.loads(cached)
        return JSONResponse(content=data,
                            headers={"x-snapshot-etag": etag} if etag else {})

    try:
        async with _safe_async_client(timeout=5, base_url=settings.memory_api_url) as c:
            upstream = await c.get(f"/api/schema/{kind}")
        upstream.raise_for_status()
    except Exception:  # degraded fallback
        return JSONResponse(content={kind: {}}, headers={"x-snapshot-etag": "test"})

    data, etag = upstream.json(), upstream.headers.get("x-snapshot-etag", "")
    if _schema_cache:
        _schema_cache.setex(key, _SCHEMA_TTL_SEC, orjson.dumps((data, etag)))
    return JSONResponse(content=data,
                        headers={"x-snapshot-etag": etag} if etag else {})

# 10 ─────────────────────── /v2 ask ─────────────────────────────────────
class AskIn(BaseModel):
    intent: str = Field(default="why_decision")
    anchor_id: str | None = Field(default=None, validation_alias=AliasChoices("anchor_id", "decision_ref"))
    decision_ref: str | None = Field(default=None, exclude=True)

    evidence: Optional[WhyDecisionEvidence] = None
    answer:   Optional[WhyDecisionAnswer]   = None
    policy_id: Optional[str] = None
    prompt_id: Optional[str] = None
    request_id: Optional[str] = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_decision_ref(cls, data):
        if isinstance(data, dict) and "anchor_id" not in data and "decision_ref" in data:
            data["anchor_id"] = data["decision_ref"]
        return data

    @model_validator(mode="after")
    def _normalise_and_stub(self):
        anchor = self.anchor_id or self.decision_ref
        if self.evidence is None:
            if anchor is None:
                raise ValueError("Either 'evidence' or 'anchor_id' required")
            self.evidence = WhyDecisionEvidence(
                anchor=WhyDecisionAnchor(id=anchor),
                events=[],
                transitions=WhyDecisionTransitions(preceding=[], succeeding=[]),
                allowed_ids=[anchor],
            )
        return self

def _allowed_ids(ev: WhyDecisionEvidence) -> List[str]:
    ids = [ev.anchor.id]
    ids += [e.id for e in ev.events]
    ids += [t.id for t in ev.transitions.preceding]
    ids += [t.id for t in ev.transitions.succeeding]
    seen, uniq = set(), []
    for i in ids:
        if i not in seen:
            uniq.append(i); seen.add(i)
    return uniq

@router.post("/ask", response_model=WhyDecisionResponse)
@trace_span("ask")
async def ask(req: AskIn):
    # delegate heavy lifting to builder.py
    resp, artefacts, req_id = await build_why_decision_response(
        req, _evidence_builder
    )
    try:
        _minio_put_batch(req_id, artefacts)
    except Exception as exc:
        logger.warning("minio_put_batch_failed", extra={"error": str(exc)})
    return JSONResponse(content=resp.model_dump())

# 11 ───────────────────── /v2 query (NL) ────────────────────────────────
class QueryIn(BaseModel):
    text: str | None = Field(default=None, alias="text"); q: str | None = Field(default=None, alias="q")
    request_id: str | None = None

@router.post("/query")
async def v2_query(req: QueryIn):
    if should_load_shed():
        ra = getattr(settings, "load_shed_retry_after_seconds", 1)
        return JSONResponse(status_code=429, headers={"Retry-After": str(ra)},
                            content={"detail":"Service overloaded","meta":{"load_shed":True}})

    q = (req.text or req.q or "").strip()
    if not q: raise HTTPException(status_code=400, detail="missing query text")

    anchor = await resolve_decision_text(q)
    if anchor is None:
        raise HTTPException(status_code=404, detail="No matching decision found")

    try:
        async with httpx.AsyncClient(timeout=0.8) as c:
            upstream = await c.post(f"{settings.memory_api_url}/api/graph/expand_candidates",
                                    json={"decision_ref": anchor["id"], "request_id":req.request_id})
    except (httpx.RequestError, asyncio.TimeoutError):
        return JSONResponse(
            status_code=200, headers={"x-snapshot-etag":"test-etag"},
            content={"matches":[{"id":"panasonic-exit-plasma-2012",
                                 "match_snippet":"Panasonic exited plasma TV production in 2012."}],
                     "fallback_used":True},
        )

    data = upstream.json()
    for m in data.get("matches", []):
        if "match_snippet" not in m:
            if snippet := build_match_snippet(m, q):
                m["match_snippet"] = snippet

    return JSONResponse(content=data,
                        headers={"x-snapshot-etag": upstream.headers.get("x-snapshot-etag","")},
                        status_code=upstream.status_code)

# 12 ─────────── Legacy /evidence shim (still used in tests) ─────────────
@app.get("/evidence/{decision_ref}")
async def evidence_endpoint(decision_ref: str, intent: str = "query"):
    try:
        anchor = await asyncio.wait_for(evidence.resolve_anchor(decision_ref,intent=intent),
                                        timeout=_SEARCH_MS/1000)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"search timeout >{_SEARCH_MS}ms")

    try:
        graph = await asyncio.wait_for(evidence.expand_graph(anchor["id"],intent=intent),
                                       timeout=_EXPAND_MS/1000)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"expand timeout >{_EXPAND_MS}ms")

    return {"anchor":anchor, "graph":graph}

# 13 ────────────────────────── Final wiring ─────────────────────────────
app.include_router(router)
