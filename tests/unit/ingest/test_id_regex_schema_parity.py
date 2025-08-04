import json, re
from importlib import resources
from ingest import cli

def test_id_regex_matches_schema_and_allows_underscore():
    schema_dir = resources.files("ingest.schemas.json_v2")
    patterns = []
    for name in ["decision.schema.json", "event.schema.json", "transition.schema.json"]:
        data = json.loads((schema_dir / name).read_text(encoding="utf-8"))
        patterns.append(data["properties"]["id"]["pattern"])
    assert all(p == "^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$" for p in patterns)
    assert cli.ID_RE.pattern == "^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$"
