import uvicorn
if __name__ == "__main__":
    uvicorn.run("gateway.app:app", host="0.0.0.0", port=8081, reload=False, log_config=None)
