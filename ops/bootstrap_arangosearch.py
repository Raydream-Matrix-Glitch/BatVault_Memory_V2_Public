#!/usr/bin/env python3
"""
Bootstrap ArangoSearch view 'nodes_search' to support resolver BM25 queries.
This is idempotent and can be run at deploy time.
"""
import sys
from arango import ArangoClient
from core_config import get_settings
from core_logging import get_logger, log_stage

logger = get_logger("ops.arangosearch")

def main():
    settings = get_settings()
    client = ArangoClient(hosts=settings.arango_url)
    sys_db = client.db("_system", username=settings.arango_root_user, password=settings.arango_root_password)
    if not sys_db.has_database(settings.arango_db):
        log_stage(logger, "ops", "db_missing", db=settings.arango_db)
        raise SystemExit(f"Database {settings.arango_db} not found")

    db = client.db(settings.arango_db, username=settings.arango_root_user, password=settings.arango_root_password)

    view_name = "nodes_search"
    if db.has_view(view_name):
        view = db.view(view_name)
        log_stage(logger, "ops", "view_exists", view=view_name)
    else:
        view = db.create_arangosearch_view(view_name, properties={})
        log_stage(logger, "ops", "view_created", view=view_name)

    # Link 'nodes' with analyzers on normative fields: rationale|description|reason|summary
    props = view.properties()
    links = props.get("links", {})
    links["nodes"] = {
        "includeAllFields": False,
        "fields": {
            "rationale": {"analyzers": ["text_en"]},
            "description": {"analyzers": ["text_en"]},
            "reason": {"analyzers": ["text_en"]},
            "summary": {"analyzers": ["text_en"]},
        },
        "storeValues": "id",
    }
    view.properties({"links": links})
    log_stage(logger, "ops", "view_linked", view=view_name, collection="nodes",
              fields=["rationale", "description", "reason", "summary"])

    # Ensure analyzer (best-effort; may be cluster-managed in some versions)
    try:
        if not db.has_analyzer("text_en"):
            db.create_analyzer(name="text_en", analyzer_type="text",
                               properties={"locale": "en.utf-8", "case": "lower", "accent": False, "stemming": True})
            log_stage(logger, "ops", "analyzer_created", analyzer="text_en")
        else:
            log_stage(logger, "ops", "analyzer_exists", analyzer="text_en")
    except Exception as e:
        log_stage(logger, "ops", "analyzer_skip", error=str(e))

    print({"ok": True, "view": view_name})

if __name__ == "__main__":
    main()
