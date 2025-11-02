# HEAD to fetch ETag + x-snapshot-etag (no body)
curl -i -sS \
  'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -X HEAD \
  -H 'X-User-Id: debug' \
  -H 'X-Policy-Key: dev-default'

# Expand (through Gateway, same headers as before)
curl -sS 'http://localhost:8081/memory/api/graph/expand_candidates' \
  -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: ceo' \
  -H 'X-Policy-Version: 0' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Request-Id: exp-$(date +%s)' \
  -H 'X-Trace-Id: exp-$(date +%s)-1' \
  -H 'X-Snapshot-ETag: b05e347ee4add0fe7f24fd5429a4dcd1dc57f59107def984758bb0e54c4a2830' \
  -d '{"anchor":"eng#d-eng-010"}' | jq .


# ENRICH (GET) — show the anchor’s materialized node via Gateway
curl -sS \
  'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: engineer' \
  -H 'X-Policy-Version: 0' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Request-Id: enr-'"$(date +%s)" \
  -H 'X-Trace-Id: enr-'"$(date +%s)"'-1' \
  -H 'X-Snapshot-ETag: b05e347ee4add0fe7f24fd5429a4dcd1dc57f59107def984758bb0e54c4a2830' \
  -H 'Accept: application/json' \
  | jq .

curl -sS \
  'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: ceo' \
  -H 'X-Policy-Version: 0' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Request-Id: enr-'"$(date +%s)" \
  -H 'X-Trace-Id: enr-'"$(date +%s)"'-1' \
  -H 'X-Snapshot-ETag: b05e347ee4add0fe7f24fd5429a4dcd1dc57f59107def984758bb0e54c4a2830' \
  -H 'Accept: application/json' \
  | jq .



# 0) Get snapshot etag (HEAD)
SNAP=$(
  curl -isS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
    -X HEAD \
    -H 'X-User-Id: debug' \
    -H 'X-User-Roles: ceo' \
    -H 'X-Policy-Key: dev-default' \
    -H 'X-Policy-Version: 0' \
    -H "X-Request-Id: head-$(date +%s)" \
    -H "X-Trace-Id: head-$(date +%s)" \
  | awk -F': ' 'tolower($1)=="x-snapshot-etag"{print $2}' | tr -d '\r'
); echo "SNAP=$SNAP"

# 1) Enrich (works; no X-Sensitivity-Ceiling)
curl -i -sS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: ceo' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: enr-$(date +%s)" \
  -H "X-Trace-Id: enr-$(date +%s)"

# 2) Expand (should now pass for CEO; still via Gateway)
curl -i -sS 'http://localhost:8081/memory/api/graph/expand_candidates' \
  -X POST \
  -H 'Content-Type: application/json' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: ceo' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: exp-$(date +%s)" \
  -H "X-Trace-Id: exp-$(date +%s)" \
  -d '{"anchor":"eng#d-eng-010"}'

# 0) Get snapshot etag (HEAD)
SNAP=$(
  curl -isS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
    -X HEAD \
    -H 'X-User-Id: debug' \
    -H 'X-User-Roles: analyst' \
    -H 'X-Policy-Key: dev-default' \
    -H 'X-Policy-Version: 0' \
    -H "X-Request-Id: head-$(date +%s)" \
    -H "X-Trace-Id: head-$(date +%s)" \
  | awk -F': ' 'tolower($1)=="x-snapshot-etag"{print $2}' | tr -d '\r'
); echo "SNAP=$SNAP"

# 1) Enrich (works; no X-Sensitivity-Ceiling)
curl -i -sS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: analyst' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: enr-$(date +%s)" \
  -H "X-Trace-Id: enr-$(date +%s)"

# 2) Expand (should now pass for CEO; still via Gateway)
curl -i -sS 'http://localhost:8081/memory/api/graph/expand_candidates' \
  -X POST \
  -H 'Content-Type: application/json' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: analyst' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: exp-$(date +%s)" \
  -H "X-Trace-Id: exp-$(date +%s)" \
  -d '{"anchor":"eng#d-eng-010"}'


  # 0) Get snapshot etag (HEAD)
SNAP=$(
  curl -isS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
    -X HEAD \
    -H 'X-User-Id: debug' \
    -H 'X-User-Roles: manager' \
    -H 'X-Policy-Key: dev-default' \
    -H 'X-Policy-Version: 0' \
    -H "X-Request-Id: head-$(date +%s)" \
    -H "X-Trace-Id: head-$(date +%s)" \
  | awk -F': ' 'tolower($1)=="x-snapshot-etag"{print $2}' | tr -d '\r'
); echo "SNAP=$SNAP"

# 1) Enrich (works; no X-Sensitivity-Ceiling)
curl -i -sS 'http://localhost:8081/memory/api/enrich?anchor=eng%23d-eng-010' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: manager' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: enr-$(date +%s)" \
  -H "X-Trace-Id: enr-$(date +%s)"

# 2) Expand (should now pass for CEO; still via Gateway)
curl -i -sS 'http://localhost:8081/memory/api/graph/expand_candidates' \
  -X POST \
  -H 'Content-Type: application/json' \
  -H "X-Snapshot-ETag: $SNAP" \
  -H 'X-User-Id: debug' \
  -H 'X-User-Roles: manager' \
  -H 'X-Policy-Key: dev-default' \
  -H 'X-Policy-Version: 0' \
  -H "X-Request-Id: exp-$(date +%s)" \
  -H "X-Trace-Id: exp-$(date +%s)" \
  -d '{"anchor":"eng#d-eng-010"}'
