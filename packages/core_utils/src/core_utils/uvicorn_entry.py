from typing import Optional
import uvicorn


def run(
    app_path: str,
    port: int,
    *,
    host: str = "0.0.0.0",
    reload: bool = False,
    log_level: str = "info",
    access_log: bool = True,
    log_config: Optional[dict] = None,
) -> None:
    uvicorn.run(
        app_path,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        access_log=access_log,
        log_config=log_config,
    )

__all__ = ["run"]