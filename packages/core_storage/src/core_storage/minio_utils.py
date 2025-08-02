from datetime import datetime

from core_logging import get_logger, log_stage
from minio.error import S3Error

logger = get_logger("minio_utils")


def ensure_bucket(client, bucket: str, retention_days: int):
    newly_created = False
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            newly_created = True
    except S3Error as exc:
        log_stage(logger, "artifacts", "minio_bucket_error", bucket=bucket, error=str(exc))
        raise

    lifecycle = f"""<LifecycleConfiguration>
  <Rule>
    <ID>batvault-artifacts-retention</ID>
    <Status>Enabled</Status>
    <Expiration><Days>{retention_days}</Days></Expiration>
  </Rule>
</LifecycleConfiguration>"""

    try:
        client.set_bucket_lifecycle(bucket, lifecycle)
    except Exception as e:
        log_stage(logger, "artifacts", "minio_lifecycle_warning", bucket=bucket, error=str(e))

    log_stage(
        logger,
        "artifacts",
        "minio_bucket_ensured",
        bucket=bucket,
        newly_created=newly_created,
        retention_days=retention_days,
        created_ts=datetime.utcnow().isoformat() if newly_created else None,
    )

    return {"bucket": bucket, "newly_created": newly_created, "retention_days": retention_days}