import uvicorn
import logging

logging.basicConfig(level="INFO")         # root logger prints to stdout
logging.getLogger("gateway").info("gateway_startup")  # one line at boot

if __name__ == "__main__":
    # Start the Gateway FastAPI app on its canonical port
    uvicorn.run(
        "gateway.app:app",
        host="0.0.0.0",
        port=8081,
        reload=False,
        log_level="info",      # restore uvicorn’s own logging
        access_log=True,       # show HTTP requests
        log_config=None,
    )