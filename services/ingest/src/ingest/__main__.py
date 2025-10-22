from __future__ import annotations
import os
from core_utils.bootstrap import ensure_monorepo_paths
ensure_monorepo_paths()
from core_utils.uvicorn_entry import run
from core_config.constants import HEALTH_PORT as PORT

LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
ACCESS_LOG = os.getenv("ACCESS_LOG", "0") in ("1","true","True","yes","on")

IS_INGEST = os.getenv("BATVAULT_INGEST_PROCESS") == "1"
if not IS_INGEST:
    from core_logging import get_logger, log_stage
    logger = get_logger("ingest.boot")
    log_stage(
        logger, "ingest", "config_error",
        error="BATVAULT_INGEST_PROCESS!=1 — shared/* excluded by import shim",
        request_id="startup",
    )
    raise SystemExit(2)

if __name__ == "__main__":
    run("ingest.app:app", port=PORT, log_level=LOG_LEVEL, access_log=ACCESS_LOG)