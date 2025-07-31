# ArangoSearch bootstrap for resolver

This repo includes `ops/bootstrap_arangosearch.py` to create/configure the `nodes_search`
view used by the resolver BM25 path.

## What it does
- Ensures database exists
- Creates the `nodes_search` ArangoSearch view (idempotent)
- Links collection `nodes` with analyzers on fields: `rationale | description | reason | summary`
- Ensures a `text_en` analyzer (if supported by your Arango client)

## Run

```bash
python ops/bootstrap_arangosearch.py
