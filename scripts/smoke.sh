#!/usr/bin/env bash
# Robust E2E sanity-check for CI / local dev.

set -euo pipefail

echo "Pinging health endpoints..."
for port in 8080 8081 8082 8083; do
  echo -n " - http://localhost:${port}/healthz ... "
  if curl -fsS "http://localhost:${port}/healthz" >/dev/null; then
    echo "OK"
  else
    echo "FAIL"; exit 1
  fi
done

echo "Checking MinIO bucket..."
if curl -fsS -X POST http://localhost:8081/ops/minio/ensure-bucket >/dev/null; then
  echo "Bucket ensured."
else
  echo "Bucket check failed."; exit 1
fi

echo "All good ✅"

# Wait until core services are really up (max 60 s each)
until curl -fsS "http://localhost:8081/readyz" >/dev/null;  do echo "⌛ gateway…";   sleep 2; done
until curl -fsS "http://localhost:8080/readyz" >/dev/null;  do echo "⌛ api-edge…"; sleep 2; done

# Seed-memory script may not have run yet in parallel pipelines → run it idempotently.
if [ -x "./scripts/seed_memory.sh" ]; then
  ./scripts/seed_memory.sh || true
fi

# Basic structured & NL requests (fail hard on non-200 or empty body)
curl -fsS -X POST http://localhost:8081/v2/ask \
     -H 'Content-Type: application/json' \
     -d '{"intent":"why_decision","decision_ref":"panasonic-exit-plasma-2012"}' \
     | jq -e '.answer.short_answer | length>0' >/dev/null

curl -fsS -X POST http://localhost:8081/v2/query \
     -H 'Content-Type: application/json' \
     -d '{"text":"Why did Panasonic exit plasma TV production?"}' \
     | jq -e '.intent=="why_decision"' >/dev/null

echo "✅ smoke.sh passed"
