import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "ingest.app:app",        # tiny FastAPI with /healthz & /readyz
        host="0.0.0.0",
        port=8083,
        reload=False,
        log_config=None,
    )