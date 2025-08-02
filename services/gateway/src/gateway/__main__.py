import logging
from core_utils.uvicorn_entry import run

logging.basicConfig(level="INFO")
logging.getLogger("gateway").info("gateway_startup")

if __name__ == "__main__":
    run("gateway.app:app", port=8081, log_level="info", access_log=True)