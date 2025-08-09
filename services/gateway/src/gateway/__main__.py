import os
import logging
from core_utils.uvicorn_entry import run
from core_config.constants import HEALTH_PORT as PORT

logging.basicConfig(level="INFO")
logging.getLogger("gateway").info("gateway_startup")


if __name__ == "__main__":
    run("gateway.app:app", port=PORT, log_level="info", access_log=False)