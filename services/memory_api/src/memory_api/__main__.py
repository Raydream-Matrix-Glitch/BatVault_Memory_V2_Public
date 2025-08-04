import os
from core_utils.uvicorn_entry import run

# ────────────────────────────────────────────────────────────────
# Default to **8000** – the canonical Memory-API port used across
# tests, configs, and gateway settings.  Still overridable via the
# same env-var to preserve one-knob configurability.
# ────────────────────────────────────────────────────────────────
PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8000"))

if __name__ == "__main__":
    run("memory_api.app:app", port=PORT, access_log=True)