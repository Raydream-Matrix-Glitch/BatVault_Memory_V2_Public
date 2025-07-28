from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse
from minio import Minio
from minio.error import S3Error
import redis
import httpx, orjson

from core_logging import get_logger, log_stage
from core_config import get_settings
from core_utils import prompt_fingerprint
from .models import (
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionAnswer,
    CompletenessFlags,
    WhyDecisionResponse,
)
from .templater import build_allowed_ids, deterministic_short_answer, validate_and_fix
import time

settings = get_settings()
logger = get_logger("gateway")

# ------------------------------------------------------------------
#  Lightweight 60-second read-through cache for schema mirrors
# ------------------------------------------------------------------
SCHEMA_CACHE_TTL = 60
try:
    _schema_cache = redis.Redis.from_url(
        settings.redis_url, decode_responses=True
    )
except Exception:
    _schema_cache = None

app = FastAPI(title="BatVault Gateway", version="0.1.0")

router = APIRouter(prefix="/v2")

# ------------------------------------------------------------------#
#  Read-through mirror of Memory-API field / relation catalogs      #
# ------------------------------------------------------------------#

@router.get("/schema/{kind}")
async def schema_mirror(kind: str):
    if kind not in ("fields", "rels"):
        raise HTTPException(status_code=404, detail="unknown schema kind")

    cache_key = f"schema:{kind}"
    if _schema_cache:
        cached = _schema_cache.get(cache_key)
        if cached:
            data, etag = orjson.loads(cached)
            resp = JSONResponse(content=data)
            if etag:
                resp.headers["x-snapshot-etag"] = etag
            return resp

    url = f"{settings.memory_api_url}/api/schema/{kind}"
    async with httpx.AsyncClient(timeout=5.0) as c:
        upstream = await c.get(url)
    if upstream.status_code != 200:
        raise HTTPException(status_code=502, detail="memory_api unavailable")

    data = upstream.json()
    etag = upstream.headers.get("x-snapshot-etag", "")
    if _schema_cache:
        _schema_cache.setex(cache_key, SCHEMA_CACHE_TTL, orjson.dumps((data, etag)))

    resp = JSONResponse(content=data)
    if etag:
        resp.headers["x-snapshot-etag"] = etag
    return resp

app.include_router(router)

def minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "gateway"}

@app.get("/readyz")
def readyz():
    # Probe MinIO + Redis to ensure we can reach dependencies
    try:
        mc = minio_client()
        _ = mc.list_buckets()  # simple reachability
        r = redis.Redis.from_url(settings.redis_url)
        r.ping()
        # Upstream Memory-API health probe
        resp = httpx.get(f"{settings.memory_api_url}/healthz", timeout=2.0)
        if resp.status_code != 200:
            raise Exception("memory_api unhealthy")
        return {"ready": True}
    except Exception:
        return {"ready": False}

@app.post("/ops/minio/ensure-bucket")
def ensure_bucket():
    """Create (if needed) and tag the artefact bucket; idempotent."""
    client = minio_client()
    bucket = settings.minio_bucket

    try:
        newly_created = False
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            newly_created = True
    except S3Error as exc:
        log_stage(logger, "artifacts", "minio_bucket_error",
                  bucket=bucket, error=str(exc))
        raise HTTPException(status_code=500,
                            detail="minio bucket check failed") from exc
    days = settings.minio_retention_days
    lifecycle = f"""
    <LifecycleConfiguration>
      <Rule>
        <ID>batvault-artifacts-retention</ID>
        <Status>Enabled</Status>
        <Expiration><Days>{days}</Days></Expiration>
      </Rule>
    </LifecycleConfiguration>
    """
    try:
        client.set_bucket_lifecycle(bucket, lifecycle)
    except Exception as e:
        log_stage(logger, "artifacts", "minio_lifecycle_warning",
                  bucket=bucket, error=str(e))

    log_stage(
        logger,
        "artifacts",
        "minio_bucket_ensured",
        bucket=bucket,
        newly_created=newly_created,
        retention_days=days,
        created_ts=datetime.utcnow().isoformat() if newly_created else None,
    )

    return JSONResponse(
      status_code=200,
      content={
          "bucket": bucket,
          "newly_created": newly_created,
          "retention_days": days,
      },
  )

# ---------- /v2/ask (templater only; contract-compliant; no LLM) ----------
@app.post("/v2/ask")
async def ask_why_decision(request: Request):
    t0 = time.perf_counter()
    body = await request.json()
    anchor_id = (
        body.get("anchor_id")
        or body.get("decision_ref")
        or body.get("anchor")
        or ""
    ).strip()
    if not anchor_id:
        raise HTTPException(status_code=400, detail="anchor_id required")

    policy_id = (body.get("policy_id") or "policy/default").strip()
    prompt_id = (body.get("prompt_id") or "templater/v0").strip()

    # Build minimal evidence (anchor-only for now)
    anchor = WhyDecisionAnchor(id=anchor_id)
    evidence = WhyDecisionEvidence(anchor=anchor)
    evidence.allowed_ids = build_allowed_ids(evidence)

    # Default supporting = {anchor}
    answer = WhyDecisionAnswer(short_answer="", supporting_ids=[anchor_id])

    # Deterministic short answer
    events_n = len(evidence.events)
    preceding_n = len(evidence.transitions.preceding)
    succeeding_n = len(evidence.transitions.succeeding)
    answer.short_answer = deterministic_short_answer(
        anchor_id, events_n, preceding_n, succeeding_n,
        len(answer.supporting_ids), len(evidence.allowed_ids)
    )

    # Validator (subset + anchor cited)
    answer, changed, errs = validate_and_fix(answer, evidence.allowed_ids, anchor_id)
    pf = prompt_fingerprint(
        {"intent":"why_decision",
         "evidence":{"anchor":{"id":anchor_id},"allowed_ids":evidence.allowed_ids}}
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "policy_id": policy_id,
        "prompt_id": prompt_id,
        "retries": 0,
        "latency_ms": latency_ms,
        "request_id": request.headers.get("x-request-id") or "",
        "prompt_fingerprint": pf,
        # Capture latest snapshot-etag so downstream clients
        # know whether their local caches are stale.
        "snapshot_etag": (
            httpx.get(
                f"{settings.memory_api_url}/api/schema/fields", timeout=3.0
            ).headers.get("x-snapshot-etag", "")
            if settings.memory_api_url
            else ""
        ),
        "fallback_used": False,
        "validator_notes": errs,
    }

    resp = WhyDecisionResponse(
        evidence=evidence,
        answer=answer,
        completeness_flags=CompletenessFlags(
            has_preceding=preceding_n > 0,
            has_succeeding=succeeding_n > 0,
            event_count=events_n
        ),
        meta=meta,
    )

    # Strategic logs
    log_stage(logger, "prompt", "templater_envelope_ready",
              request_id=meta["request_id"], prompt_fingerprint=pf,
              allowed_ids=len(evidence.allowed_ids))
    log_stage(logger, "validate", "templater_answer_validated",
              request_id=meta["request_id"], changed=changed, errors=errs)
    log_stage(logger, "render", "templater_answer_rendered",
              request_id=meta["request_id"], latency_ms=latency_ms)

    return JSONResponse(resp.model_dump())
