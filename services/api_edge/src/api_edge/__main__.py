import os
import sys
import logging
from pathlib import Path

# Make sure monorepo paths are active even if PYTHONPATH is minimal.
# This guarantees 'packages/*/src' and 'services/*/src' are importable.
ROOT = Path(__file__).resolve().parents[4]  # -> /app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    # Adds /app, /app/packages/*/src, /app/services/*/src at interpreter startup.
    import sitecustomize  # noqa: F401
except Exception:
    # Non-fatal: subsequent imports will raise clearly if anything's missing.
    pass

from core_utils.uvicorn_entry import run
from core_config.constants import HEALTH_PORT as PORT

if __name__ == "__main__":
    run("api_edge.app:app", port=PORT, log_level="info", access_log=False)

PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8080"))
