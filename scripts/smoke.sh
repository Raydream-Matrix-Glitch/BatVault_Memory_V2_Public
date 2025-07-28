#!/usr/bin/env bash
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
if curl -fsS http://localhost:8080/ops/minio/bucket >/dev/null; then
  echo "Bucket ensured."
else
  echo "Bucket check failed."; exit 1
fi

echo "All good ✅"
