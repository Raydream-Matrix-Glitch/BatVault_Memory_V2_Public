from datetime import datetime

from core_logging import get_logger, log_stage
try:
    from minio.error import S3Error
except Exception:  # pragma: no cover – test stubs may not install minio
    class S3Error(Exception):  # minimal stand-in so tests can run
        pass
try:
    # needed for lifecycle policy; tests use a stub client but these names must exist
    from minio.lifecycle import LifecycleConfig, Rule, Expiration, Filter, ENABLED
except Exception:  # pragma: no cover
    LifecycleConfig = Rule = Expiration = Filter = None
    ENABLED = "Enabled"

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

    try:
        log_stage(logger, "artifacts", "minio_lifecycle_config_begin", bucket=bucket, retention_days=retention_days)
        if None in (LifecycleConfig, Rule, Expiration, Filter):
            raise RuntimeError("lifecycle_sdk_missing")
        rule = Rule(
            rule_id="batvault-artifacts-retention",
            status=ENABLED,
            rule_filter=Filter(prefix=""),
            expiration=Expiration(days=retention_days),
        )
        lifecycle_config = LifecycleConfig([rule])
        client.set_bucket_lifecycle(bucket, lifecycle_config)
        log_stage(logger, "artifacts", "minio_lifecycle_config_applied", bucket=bucket)
    except Exception as e:
        # Log and continue; this warning means the lifecycle could not be set
        # but the bucket still exists.  The error message will include
        # diagnostic information from the SDK.
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