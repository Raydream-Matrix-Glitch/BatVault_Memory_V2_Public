from __future__ import annotations

import redis, time
import asyncio
from core_utils import jsonx
from pathlib import Path
from types import SimpleNamespace

from core_utils.snapshot import compute_snapshot_etag_for_files
from core_logging import get_logger, log_stage
from core_metrics import counter as _metric_counter
from core_config import get_settings

# ── optional Redis cache for snapshot_etag, field-catalog, etc. ─────────────
import redis, time

_CACHE_TTL = 60  # seconds

def _redis():
    try:
        return redis.Redis.from_url(get_settings().redis_url)
    except Exception:
        return None

def _cache_get(key: str):
    r = _redis()
    if not r:
        return None
    val = r.get(key)
    if val is not None:
        _metric_counter("cache_hit_total", 1, service="ingest")
        return jsonx.loads(val)
    _metric_counter("cache_miss_total", 1, service="ingest")
    return None

def _cache_set(key: str, value, ttl: int = _CACHE_TTL):
    r = _redis()
    if not r:
        return
    _metric_counter("cache_write_total", 1, service="ingest")
    r.setex(key, ttl, jsonx.dumps(value))

# Initialize structured logger with ingest service context
logger = get_logger("ingest.watcher")

class SnapshotWatcher:
    def __init__(
        self,
        app,
        *,
        root_dir: str | Path,
        pattern: str = "**/*.json",
        poll_interval: float = 2.0,
    ) -> None:
        self.app = app
        self.root_dir = Path(root_dir)
        self.pattern = pattern
        self.poll_interval = poll_interval
        self._last_etag: str | None = None

    # ---------- pure helpers ------------------------------------------------
    def _collect_files(self) -> list[str]:
        return [str(p) for p in self.root_dir.glob(self.pattern)]

    def compute_etag(self) -> str | None:
        files = self._collect_files()
        return compute_snapshot_etag_for_files(files) if files else None

    # ---------- side-effect helpers ----------------------------------------
    def tick(self) -> str | None:
        """
        Single poll-iteration: recompute the hash and, if it changed,
        push it to `app.state.snapshot_etag`.
        """
        etag = self.compute_etag()
        if etag and etag != self._last_etag:
            # emit a structured event for Kibana/dashboards
            log_stage(
                logger,
                "ingest",
                "new_snapshot",
                snapshot_etag=etag,
                file_count=len(self._collect_files()),
            )
            setattr(self.app.state, "snapshot_etag", etag)
            self._last_etag = etag
        return etag

    # ---------- background loop --------------------------------------------
    async def start(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(self.poll_interval)


# ---------------------------------------------------------------------------
# Convenience helper so unit tests can get a minimal “app” without FastAPI
# ---------------------------------------------------------------------------
def _dummy_app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())
