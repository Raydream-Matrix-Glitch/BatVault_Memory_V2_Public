#!/bin/sh
set -eu
ARANGO_HOST="${ARANGO_HOST:-http://arangodb:8529}"
echo "[bootstrap] starting against ${ARANGO_HOST}"

# Wait until Arango responds
i=0
until curl -fsS "${ARANGO_HOST}/_api/version" >/dev/null 2>&1; do
  i=$((i+1))
  if [ "$i" -gt 60 ]; then
    echo "[bootstrap] arango not ready after 60s" >&2
    exit 1
  fi
  sleep 1
done
fi

# Fallback to curl-based idempotent bootstrap
echo "[bootstrap] creating analyzer text_en (if absent)"
curl -sS -X POST "${ARANGO_HOST}/_api/analyzer" -H "content-type: application/json" \
  -d '{"name":"text_en","type":"text","properties":{"locale":"en.utf-8","case":"lower","accent":false,"stemming":true}}' || true

echo "[bootstrap] creating view nodes_search (if absent)"
curl -sS -X POST "${ARANGO_HOST}/_api/view" -H "content-type: application/json" \
  -d '{"name":"nodes_search","type":"arangosearch"}' || true

echo "[bootstrap] updating view links"
curl -sS -X PATCH "${ARANGO_HOST}/_api/view/nodes_search/properties" -H "content-type: application/json" \
  -d '{"links":{"nodes":{"includeAllFields":false,"fields":{"rationale":{"analyzers":["text_en"]},"description":{"analyzers":["text_en"]},"reason":{"analyzers":["text_en"]},"summary":{"analyzers":["text_en"]}},"storeValues":"id"}}}' || true

echo "[bootstrap] done"
