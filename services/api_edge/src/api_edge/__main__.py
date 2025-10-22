from __future__ import annotations
import os
from core_utils.bootstrap import ensure_monorepo_paths
ensure_monorepo_paths()
from core_utils.uvicorn_entry import run
from core_config.constants import HEALTH_PORT as PORT

LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
ACCESS_LOG = os.getenv("ACCESS_LOG", "0") in ("1","true","True","yes","on")

if __name__ == "__main__":
    run("api_edge.app:app", port=PORT, log_level=LOG_LEVEL, access_log=ACCESS_LOG)