from __future__ import annotations
import io
import tarfile
from datetime import datetime, timezone
import os
import time
from typing import Dict, Tuple
from core_utils import jsonx
from core_logging import get_logger, log_stage
from core_utils.backoff import compute_backoff_delay_ms

logger = get_logger("artifact_index")

def build_named_bundles(artefacts: Dict[str, bytes], bundle_map: Dict[str, list[str]]) -> Tuple[Dict[str, bytes], bytes]:
    """
    Build multiple named .tar.gz bundles from the given artefacts.

    :param artefacts: mapping of filename -> bytes
    :param bundle_map: mapping of bundle_name -> list of filenames to include
    :return: (bundles: dict[str, bytes], meta_bytes)
    """
    now = datetime.now(timezone.utc).isoformat()
    bundles: Dict[str, bytes] = {}
    meta = {"generated_at": now, "bundles": {}}
    for name, names in bundle_map.items():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for fn in names:
                blob = artefacts.get(fn)
                if blob is None:
                    continue
                info = tarfile.TarInfo(name=fn)
                info.size = len(blob)
                tar.addfile(info, io.BytesIO(blob))
        payload = buf.getvalue()
        bundles[name] = payload
        meta["bundles"][name] = {"size_bytes": len(payload), "files": names}
    meta_bytes = jsonx.dumps(meta).encode("utf-8")
    return bundles, meta_bytes

def upload_named_bundles(client, bucket: str, request_id: str, bundles: Dict[str, bytes], meta_bytes: bytes) -> None:
    """
    Upload multiple bundles and a shared meta sidecar to object storage.
    """
    # Env-driven retry knobs (fall back to HTTP defaults if provided there)
    max_retries = int(os.getenv("MINIO_MAX_RETRIES", "3"))
    base_ms = int(os.getenv("MINIO_RETRY_BASE_MS", os.getenv("HTTP_RETRY_BASE_MS", "50")))
    jitter_ms = int(os.getenv("MINIO_RETRY_JITTER_MS", os.getenv("HTTP_RETRY_JITTER_MS", "200")))
    cap_ms = int(os.getenv("MINIO_RETRY_CAP_MS", "2000"))

    def _retry(op: str, fn, *args, **kwargs):
        last_exc = None
        for attempt in range(1, max_retries + 2):  # retries=N → attempts=N+1
            try:
                return fn(*args, **kwargs)
            except (OSError, RuntimeError) as e:
                last_exc = e
                if attempt <= max_retries:
                    delay_ms = compute_backoff_delay_ms(attempt, base_ms=base_ms, jitter_ms=jitter_ms, cap_ms=cap_ms, mode="exp_equal_jitter")
                    # Strategic retry sleep log (keeps your pattern)
                    log_stage(
                        logger, "artifacts", "artifacts.retry_sleep",
                        request_id=request_id, op=op, attempt=attempt, delay_ms=delay_ms
                    )
                    time.sleep(delay_ms / 1000.0)
                    continue
                break
        assert last_exc is not None
        # Terminal failure is surfaced; caller decides whether to handle
        log_stage(logger, "artifacts", "named_bundles_upload_failed", request_id=request_id, error=str(last_exc))
        raise last_exc

    # Ensure bucket exists (idempotent; may already exist)
    try:
        _retry("make_bucket", client.make_bucket, bucket)
    except (OSError, RuntimeError):
        # Continue if bucket already exists or creation is racing elsewhere
        pass

    # Upload bundles
    for name, blob in bundles.items():
        _retry(
            "put_object",
            client.put_object,
            bucket,
            f"{request_id}/{name}.tar.gz",
            io.BytesIO(blob),
            length=len(blob),
            content_type="application/gzip",
        )
    # Per-request meta for list views
    _retry(
        "put_object",
        client.put_object,
        bucket,
        f"{request_id}/_index.json",
        io.BytesIO(meta_bytes),
        length=len(meta_bytes),
        content_type="application/json",
    )
    log_stage(logger, "artifacts", "named_bundles_upload_ok",
              request_id=request_id, bundle_count=len(bundles))

def build_bundle_and_meta(artefacts: Dict[str, bytes]) -> Tuple[bytes, bytes]:
    """
    Build a .tar.gz bundle of the given artefacts and a compact meta.json describing them.
    Returns (bundle_bytes, meta_bytes).
    """
    now = datetime.now(timezone.utc).isoformat()
    total_bytes = sum(len(b) for b in artefacts.values())
    index = {
        "created_at": now,
        "object_count": len(artefacts),
        "bytes_total": total_bytes,
        "files": [{"name": k, "bytes": len(v)} for k, v in artefacts.items()],
        "schema": "artifact_index@1",
    }

    # Opportunistically enrich the index with snapshot/anchor info for auditability.
    try:
        resp_json_b = artefacts.get("response.json")
        if isinstance(resp_json_b, (bytes, bytearray)):
            resp = jsonx.loads(resp_json_b.decode("utf-8"))
            snap = resp.get("meta", {}).get("snapshot_etag")
            if snap:
                index["snapshot_etag"] = snap
            anchor_id = resp.get("evidence", {}).get("anchor", {}).get("id")
            if anchor_id:
                index["anchor_id"] = anchor_id
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        # Non-fatal; preserve minimal index when parsing fails
        pass

    # Create bundle in-memory
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # include meta.json inside archive as well
        meta_inside = jsonx.dumps(index).encode("utf-8")
        ti = tarfile.TarInfo(name="_meta.json")
        ti.size = len(meta_inside)
        ti.mtime = int(datetime.now().timestamp())
        tf.addfile(ti, io.BytesIO(meta_inside))

        for name, blob in artefacts.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(blob)
            ti.mtime = int(datetime.now().timestamp())
            tf.addfile(ti, io.BytesIO(blob))
    bundle_bytes = buf.getvalue()
    meta_bytes = jsonx.dumps(index).encode("utf-8")
    return bundle_bytes, meta_bytes


def upload_bundle_and_meta(client, bucket: str, request_id: str, bundle_bytes: bytes, meta_bytes: bytes) -> None:
    """
    Upload the bundle (.tar.gz) and the sidecar meta JSON to the given bucket.
    Safe to call in a best-effort manner; emits structured logs.
    """
    # Use the same retry knobs as upload_named_bundles
    max_retries = int(os.getenv("MINIO_MAX_RETRIES", "3"))
    base_ms = int(os.getenv("MINIO_RETRY_BASE_MS", os.getenv("HTTP_RETRY_BASE_MS", "50")))
    jitter_ms = int(os.getenv("MINIO_RETRY_JITTER_MS", os.getenv("HTTP_RETRY_JITTER_MS", "200")))
    cap_ms = int(os.getenv("MINIO_RETRY_CAP_MS", "2000"))

    def _retry(op: str, fn, *args, **kwargs):
        last_exc = None
        for attempt in range(1, max_retries + 2):
            try:
                return fn(*args, **kwargs)
            except (OSError, RuntimeError) as e:
                last_exc = e
                if attempt <= max_retries:
                    delay_ms = compute_backoff_delay_ms(attempt, base_ms=base_ms, jitter_ms=jitter_ms, cap_ms=cap_ms, mode="exp_equal_jitter")
                    log_stage(logger, "artifacts", "artifacts.retry_sleep", request_id=request_id, op=op, attempt=attempt, delay_ms=delay_ms)
                    time.sleep(delay_ms / 1000.0)
                    continue
                break
        assert last_exc is not None
        return last_exc

    # Upload archive (retrying)
    err = _retry(
        "put_object",
        client.put_object,
        bucket,
        f"{request_id}.bundle.tar.gz",
        io.BytesIO(bundle_bytes),
        length=len(bundle_bytes),
        content_type="application/gzip",
    )
    if isinstance(err, Exception):
        # Non-fatal – preserve prior best-effort behavior
        log_stage(logger, "artifacts", "index_upload_failed", request_id=request_id, error=str(err))
        return

    # Upload sidecar meta JSON (root)
    err = _retry(
        "put_object",
        client.put_object,
        bucket,
        f"{request_id}.meta.json",
        io.BytesIO(meta_bytes),
        length=len(meta_bytes),
        content_type="application/json",
    )
    if isinstance(err, Exception):
        log_stage(logger, "artifacts", "index_upload_failed", request_id=request_id, error=str(err))
        return

    # Also write a per-prefix meta object so folder views show dates/size
    err = _retry(
        "put_object",
        client.put_object,
        bucket,
        f"{request_id}/_meta.json",
        io.BytesIO(meta_bytes),
        length=len(meta_bytes),
        content_type="application/json",
    )
    if isinstance(err, Exception):
        log_stage(logger, "artifacts", "index_upload_failed", request_id=request_id, error=str(err))
        return

    log_stage(
        logger, "artifacts", "index_upload_ok",
        request_id=request_id, bundle_bytes=len(bundle_bytes), meta_bytes=len(meta_bytes)
    )