import hashlib
import os
from typing import Iterable

def compute_snapshot_etag(chunks: Iterable[bytes]) -> str:
    """
    Deterministic content hash over provided byte chunks.
    removes timestamp salt so the same content yields the same ETag,
    improving reproducibility and cacheability.
    """
    h = hashlib.sha256()
    for b in chunks:
        if not b:
            continue
        h.update(b)
    return h.hexdigest()

def compute_snapshot_etag_for_files(paths: list[str]) -> str:
    """
    Deterministic, memory-efficient ETag for a set of files.
    - Streams files in chunks (no whole-file loads).
    - Includes file path and size to defend against hash collisions from
      concatenation across differently ordered inputs.
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        # incorporate filename and size to bind content to its origin
        try:
            st = os.stat(p)
            h.update(p.encode("utf-8"))
            h.update(str(st.st_size).encode("ascii"))
        except Exception:
            # if stat fails, still include the path for stability
            h.update(p.encode("utf-8"))
        try:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
        except FileNotFoundError:
            # ignore missing files – they simply don't contribute
            continue
    return h.hexdigest()
