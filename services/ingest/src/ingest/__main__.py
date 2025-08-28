import os
from core_utils.uvicorn_entry import run

PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8083"))

if __name__ == "__main__":
    run("ingest.app:app", port=PORT, access_log=True)