import hashlib, time
from typing import Iterable

def compute_snapshot_etag(chunks: Iterable[bytes]) -> str:
    """
    Spec: sha256(content) + timestamp (seconds) => hex digest
    """
    h = hashlib.sha256()
    for b in chunks:
        h.update(b)
    h.update(str(int(time.time())).encode())
    return h.hexdigest()

def compute_snapshot_etag_for_files(paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        with open(p, "rb") as f:
            h.update(f.read())
    h.update(str(int(time.time())).encode())
    return h.hexdigest()
