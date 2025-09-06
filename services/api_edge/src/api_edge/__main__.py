import os
import sys
from pathlib import Path

# Make sure monorepo paths are active even if PYTHONPATH is minimal.
# This guarantees 'packages/*/src' and 'services/*/src' are importable.
ROOT = Path(__file__).resolve().parents[4]  # -> /app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_utils.uvicorn_entry import run

if __name__ == "__main__":
    run("api_edge.app:app", port=int(os.getenv("API_EDGE_PORT","8080")), log_level="info", access_log=False)
