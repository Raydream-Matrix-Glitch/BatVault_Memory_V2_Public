import uvicorn
if __name__ == "__main__":
    uvicorn.run("memory_api.app:app", host="0.0.0.0", port=8082, reload=False, log_config=None)
