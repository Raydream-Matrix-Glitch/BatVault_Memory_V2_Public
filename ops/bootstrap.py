#!/usr/bin/env python3
"""
Single source of truth for Arango bootstrap.
Waits for the server, then instantiates ArangoStore with lazy=False,
which triggers the full schema / index setup.
"""
import time, sys
from arango import ArangoClient
try:
    from arango.exceptions import ArangoError, ServerConnectionError  # type: ignore
except Exception:  # pragma: no cover (older clients)
    ArangoError = RuntimeError  # type: ignore
    ServerConnectionError = ConnectionError  # type: ignore
from core_config import get_settings
from core_storage.arangodb import ArangoStore
from core_logging import get_logger, log_stage
logger = get_logger("ops.bootstrap")

def _wait(url: str,
          user: str,
          pwd: str,
          seconds: int = 180) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            client = ArangoClient(hosts=url)
            sys_db = client.db("_system", username=user, password=pwd)
            if sys_db.version(): 
                return
        except (ServerConnectionError, ArangoError, OSError, ConnectionError) as exc:
            # Surface why we cannot connect in `docker compose logs`
            print(f"🔄  Waiting for ArangoDB @ {url} – {exc}", file=sys.stderr, flush=True)
            time.sleep(2)
    sys.exit(f"ArangoDB not reachable after {seconds} s")

if __name__ == "__main__":
    cfg = get_settings()
    try:
        log_stage(
            logger, "bootstrap", "start",
            arango_url=cfg.arango_url, db=cfg.arango_db, graph=cfg.arango_graph_name,
            request_id="startup",
        )
        _wait(cfg.arango_url,
              cfg.arango_root_user,
              cfg.arango_root_password)
        ArangoStore(
            url=cfg.arango_url,
            root_user=cfg.arango_root_user,
            root_password=cfg.arango_root_password,
            db_name=cfg.arango_db,
            graph_name=cfg.arango_graph_name,
            catalog_col=cfg.arango_catalog_collection,
            meta_col=cfg.arango_meta_collection,
            lazy=False,                # force immediate bootstrap
        )
        log_stage(
            logger, "bootstrap", "end",
            arango_url=cfg.arango_url, db=cfg.arango_db, graph=cfg.arango_graph_name,
            status="ok", request_id="startup",
        )
        print("✅  Arango bootstrap complete")
    except (ArangoError, OSError, ConnectionError, RuntimeError, ValueError) as exc:
        log_stage(
            logger, "bootstrap", "failed",
            error=type(exc).__name__, request_id="startup",
        )
        raise
