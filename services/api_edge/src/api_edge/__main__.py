import uvicorn
if __name__ == "__main__":
    uvicorn.run("api_edge.app:app", host="0.0.0.0", port=8080, reload=False, log_config=None)
