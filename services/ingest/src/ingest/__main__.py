from core_utils.uvicorn_entry import run

if __name__ == "__main__":
    run("ingest.app:app", port=8083, access_log=True)