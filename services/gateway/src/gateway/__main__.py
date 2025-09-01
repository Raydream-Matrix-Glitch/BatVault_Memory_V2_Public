import sys
from pathlib import Path

# Ensure the monorepo import shim is active even if PYTHONPATH is minimal.
# This guarantees 'packages/*/src' and 'services/*/src' are importable.
ROOT = Path(__file__).resolve().parents[4]  # -> /app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_utils.uvicorn_entry import run
from core_config.constants import HEALTH_PORT as PORT

if __name__ == "__main__":
    run("gateway.app:app", port=PORT, log_level="info", access_log=False)