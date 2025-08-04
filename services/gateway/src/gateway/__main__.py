import os
import logging
from core_utils.uvicorn_entry import run

logging.basicConfig(level="INFO")
logging.getLogger("gateway").info("gateway_startup")

PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8081"))

if __name__ == "__main__":
    run("gateway.app:app", port=PORT, log_level="info", access_log=False)