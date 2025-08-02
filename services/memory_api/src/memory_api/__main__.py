from core_utils.uvicorn_entry import run

if __name__ == "__main__":
    run("memory_api.app:app", port=8082, access_log=True)