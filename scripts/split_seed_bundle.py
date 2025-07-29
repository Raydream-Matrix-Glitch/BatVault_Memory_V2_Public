#!/usr/bin/env python3
"""
Usage: python split_seed_bundle.py test_node.json memory/fixtures
Creates:
  memory/fixtures/events/<id>.json
  memory/fixtures/decisions/<id>.json
  memory/fixtures/transitions/<id>.json
"""

import json, sys, pathlib

bundle = pathlib.Path(sys.argv[1])
dest   = pathlib.Path(sys.argv[2])

with bundle.open() as fh:
    data = json.load(fh)

for kind in ("events", "decisions", "transitions"):
    for doc in data.get(kind, []):
        out = dest / kind / f"{doc['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=2))
        print("wrote", out)
