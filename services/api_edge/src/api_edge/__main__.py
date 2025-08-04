import os
import uvicorn

PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8080"))

if __name__ == "__main__":
    uvicorn.run("api_edge.app:app", host="0.0.0.0", port=PORT, reload=False, log_config=None)
