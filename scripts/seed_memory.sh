#!/usr/bin/env bash
set -euo pipefail

echo "Seeding memory fixtures..."
docker compose exec -T ingest python -m ingest.cli seed /app/memory/fixtures

echo "Done."
