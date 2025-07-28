from fastapi import FastAPI
from fastapi.responses import JSONResponse
from core_logging import get_logger, log_event
from core_config import get_settings
from minio import Minio
from datetime import timedelta

settings = get_settings()
logger = get_logger("gateway")

app = FastAPI(title="BatVault Gateway", version="0.1.0")

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "gateway"}

@app.post("/ops/minio/ensure-bucket")
def ensure_bucket():
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )
    bucket = settings.minio_bucket
    created = False
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        created = True
    # Put lifecycle policy for retention
    days = settings.minio_retention_days
    lifecycle = f"""
    <LifecycleConfiguration>
      <Rule>
        <ID>batvault-artifacts-retention</ID>
        <Status>Enabled</Status>
        <Expiration>
          <Days>{days}</Days>
        </Expiration>
      </Rule>
    </LifecycleConfiguration>
    """
    try:
        client.set_bucket_lifecycle(bucket, lifecycle)
    except Exception as e:
        log_event(logger, "minio_lifecycle_warning", error=str(e))

    log_event(logger, "minio_bucket_ensured", bucket=bucket, created=created, retention_days=days)
    return JSONResponse({"bucket": bucket, "created": created, "retention_days": days})
