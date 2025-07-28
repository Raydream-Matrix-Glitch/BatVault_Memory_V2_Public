from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from minio import Minio
from minio.error import S3Error
import redis

from core_logging import get_logger, log_stage
from core_config import get_settings

settings = get_settings()
logger = get_logger("gateway")
app = FastAPI(title="BatVault Gateway", version="0.1.0")

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
