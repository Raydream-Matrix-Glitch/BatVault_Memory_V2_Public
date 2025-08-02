#!/usr/bin/env python3
"""
Single source of truth for Arango bootstrap.
Waits for the server, then instantiates ArangoStore with lazy=False,
which triggers the full schema / index setup.
"""
import os, time, sys
from arango import ArangoClient
from core_config import get_settings
from core_storage.arangodb import ArangoStore

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
        except Exception as exc:
            # Surface the *real* reason in `docker compose logs`
            print(f"🔄  Waiting for ArangoDB @ {url} – {exc}", file=sys.stderr, flush=True)
            time.sleep(2)
        except Exception as exc:
            # Surface *why* we cannot connect in `docker compose logs`
            print(f"🔄  Waiting for ArangoDB @ {url} – {exc}",
                  file=sys.stderr, flush=True)
            time.sleep(2)
    sys.exit(f"ArangoDB not reachable after {seconds} s")

if __name__ == "__main__":
    cfg = get_settings()
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
    print("✅  Arango bootstrap complete")
