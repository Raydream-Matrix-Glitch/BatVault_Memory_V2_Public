from __future__ import annotations

import io
import tarfile
from datetime import datetime, timezone
from typing import Dict, Tuple

from core_utils import jsonx
from core_logging import get_logger, log_stage

logger = get_logger("artifact_index")

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
    except Exception:
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
    try:
        # Upload archive
        client.put_object(
            bucket,
            f"{request_id}.bundle.tar.gz",
            io.BytesIO(bundle_bytes),
            length=len(bundle_bytes),
            content_type="application/gzip",
        )
        # Upload sidecar meta JSON (root)
        client.put_object(
            bucket,
            f"{request_id}.meta.json",
            io.BytesIO(meta_bytes),
            length=len(meta_bytes),
            content_type="application/json",
        )
        # Also write a per-prefix meta object so inspecting the folder shows dates and size
        client.put_object(
            bucket,
            f"{request_id}/_meta.json",
            io.BytesIO(meta_bytes),
            length=len(meta_bytes),
            content_type="application/json",
        )
        log_stage(logger, "artifacts", "index_upload_ok",
                  request_id=request_id, bundle_bytes=len(bundle_bytes), meta_bytes=len(meta_bytes))
    except Exception as exc:
        # Non-fatal – raw artefacts were already written
        log_stage(logger, "artifacts", "index_upload_failed", request_id=request_id, error=str(exc))