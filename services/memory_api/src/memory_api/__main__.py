import os
import os
import sys
from pathlib import Path

# Ensure the monorepo import shim is active even if PYTHONPATH is minimal.
# This guarantees 'packages/*/src' and 'services/*/src' (e.g., core_utils) are importable.
ROOT = Path(__file__).resolve().parents[4]  # -> /app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    import sitecustomize  # adds /app/packages/*/src and /app/services/*/src
except Exception:
    # Non-fatal: if this fails, subsequent imports will raise clearly.
    pass

from core_utils.uvicorn_entry import run

# ────────────────────────────────────────────────────────────────
# Default to **8000** – the canonical Memory-API port used across
# tests, configs, and gateway settings.  Still overridable via the
# same env-var to preserve one-knob configurability.
# ────────────────────────────────────────────────────────────────
PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8000"))

if __name__ == "__main__":
    run("memory_api.app:app", port=PORT, access_log=True)