from __future__ import annotations
import redis, time, os
from redis.exceptions import RedisError
import asyncio
from core_utils import jsonx
from pathlib import Path
from types import SimpleNamespace
from core_utils.snapshot import compute_snapshot_etag_for_files
from core_logging import get_logger, log_stage
from core_metrics import counter as _metric_counter
from core_http.client import get_http_client
from core_config import get_settings

_CACHE_TTL = 60  # seconds
_REDIS_WARNED = False  # ensure we only emit one structured error log

def _redis():
    global _REDIS_WARNED
    try:
        return redis.Redis.from_url(get_settings().redis_url)
    except (RedisError, ValueError) as e:
        if not _REDIS_WARNED:
            # Structured, one-time observability for optional cache outage
            log_stage(
                get_logger("ingest.watcher"), "ingest", "redis_unavailable",
                error=str(e), request_id="watcher_boot"
            )
            _REDIS_WARNED = True
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
    try:
        r.setex(key, ttl, jsonx.dumps(value))
    except RedisError as e:
        log_stage(
            logger, "ingest", "redis_set_failed",
            error=str(e), request_id="watcher_boot"
        )

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
            # -------- Prewarm hook (deterministic, config-driven) -------
            path = os.getenv("PREWARM_TOPK_PATH", os.path.join("policy", "prewarm.json"))
            anchors: list[str] = []
            policy_headers: dict[str, str] = {}
            try:
                if Path(path).exists():
                    data = jsonx.loads(Path(path).read_text(encoding="utf-8"))
                    anchors = list((data or {}).get("anchors") or [])
                    policy_headers = dict((data or {}).get("policy_headers") or {})
                else:
                    log_stage(logger, "prewarm", "config_missing", path=path, request_id=etag)
            except (OSError, ValueError) as e:
                log_stage(logger, "prewarm", "config_read_failed", path=path, error=str(e), request_id=etag)
                anchors, policy_headers = [], {}
            if anchors:
                gw = getattr(get_settings(), "gateway_url", None)
                if gw:
                    async def _prewarm_async():
                        client = get_http_client(timeout_ms=1500)
                        resp = await client.post(
                            f"{gw}/v2/prewarm",
                            json={"anchors": anchors, "policy_headers": policy_headers},
                        )
                        status = int(getattr(resp, "status_code", 0) or 0)
                        if status < 300:
                            log_stage(logger, "prewarm", "enqueued", count=len(anchors), request_id=etag)
                        else:
                            log_stage(logger, "prewarm", "enqueue_failed", status=status, request_id=etag)
                    try:
                        asyncio.get_running_loop().create_task(_prewarm_async())
                    except RuntimeError:
                        # No running loop (e.g., unit tests): run synchronously
                        asyncio.run(_prewarm_async())
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
