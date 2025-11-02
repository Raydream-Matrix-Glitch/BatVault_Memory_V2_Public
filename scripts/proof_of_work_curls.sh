#!/usr/bin/env bash
# proof_of_work_curls.sh
set -euo pipefail
export LC_ALL=C

# -------- 0) Config --------
HOST="${HOST:-http://localhost:8081}"                 # gateway
MEMORY_HOST="${MEMORY_HOST:-http://localhost:8000}"   # memory_api direct

USER_ID="${USER_ID:-debug}"
USER_ROLES_CEO="${USER_ROLES_CEO:-ceo}"
USER_ROLES_MGR="${USER_ROLES_MGR:-manager}"
POLICY_KEY="${POLICY_KEY:-dev-default}"
POLICY_VERSION="${POLICY_VERSION:-0}"

ANCHOR_ID="${ANCHOR_ID:-eng#d-eng-010}"
ANCHOR_ID_2="${ANCHOR_ID_2:-eng#d-eng-011}"
ANCHOR_Q="$(printf '%s' "$ANCHOR_ID" | sed 's/#/%23/g')"

# read-only, known-good bundle we already have in storage
FIXED_BUNDLE_REQ_ID_DEFAULT="894778621ccf410e"
FIXED_BUNDLE_REQ_ID="${FIXED_BUNDLE_REQ_ID:-$FIXED_BUNDLE_REQ_ID_DEFAULT}"

# per-run req id for *new* writes (v3/query etc.)
RUN_REQ_ID="${RUN_REQ_ID:-run-$(date +%s)-$RANDOM}"

rid() { printf '%s-%04d' "$(date +%s)" "$((RANDOM % 10000))"; }
tid() { printf 'trace-%s-%04d' "$(date +%s)" "$((RANDOM % 10000))"; }
divider(){ printf '\n==================== %s ====================\n' "$1"; }

get_etag_and_snap () {
  local ROLE=${1:-$USER_ROLES_CEO}
  local RAW
  RAW=$(
    curl -isS "$HOST/memory/api/enrich?anchor=$ANCHOR_Q" -X HEAD \
      -H "X-User-Id: $USER_ID" \
      -H "X-User-Roles: $ROLE" \
      -H "X-Policy-Key: $POLICY_KEY" \
      -H "X-Policy-Version: $POLICY_VERSION" \
      -H "X-Request-Id: head-$(rid)" \
      -H "X-Trace-Id: head-$(tid)"
  )
  local ETAG SNAP
  ETAG=$(printf '%s\n' "$RAW" | awk -F': ' 'tolower($1)=="etag"{gsub("\"","",$2);gsub("\r","",$2);print $2}')
  SNAP=$(printf '%s\n' "$RAW" | awk -F': ' 'tolower($1)=="x-snapshot-etag"{gsub("\r","",$2);print $2}')
  printf '%s %s\n' "$ETAG" "$SNAP"
}

get_snap () {
  get_etag_and_snap "${1:-$USER_ROLES_CEO}" | awk '{print $2}'
}

discover_event_anchor () {
  local SNAP ROLE="ceo"
  SNAP=$(get_snap "$ROLE")
  local CANDIDATE
  CANDIDATE=$(
    curl -sS "$HOST/memory/api/graph/expand_candidates" -X POST \
      -H "Content-Type: application/json" \
      -H "X-Snapshot-ETag: $SNAP" \
      -H "X-User-Id: $USER_ID" -H "X-User-Roles: $ROLE" \
      -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
      -H "X-Request-Id: disc-$(rid)" -H "X-Trace-Id: disc-$(tid)" \
      -d '{"anchor":"'"$ANCHOR_ID"'"}' \
    | jq -r '
        .graph as $g
        | ( ($g.edges // []) | map(.from, .to) | map(select(type=="string")) | unique | map(select(test("#e-"))) ) as $events
        | ( ($g.nodes // []) | map(.id) | map(select(type=="string")) | unique | map(select(test("#e-"))) ) as $event_nodes
        | ( ($events + $event_nodes) | unique | .[0] ) // ""
      '
  )
  if [ -z "$CANDIDATE" ]; then
    printf ''
    return 0
  fi
  local CAND_Q HTTP
  CAND_Q="$(printf '%s' "$CANDIDATE" | sed 's/#/%23/g')"
  HTTP=$(
    curl -sS "$HOST/memory/api/enrich/event?anchor=$CAND_Q" \
      -H "X-Snapshot-ETag: $SNAP" \
      -H "X-User-Id: $USER_ID" -H "X-User-Roles: $ROLE" \
      -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
      -H "X-Request-Id: event-probe-$(rid)" -H "X-Trace-Id: event-probe-$(tid)" \
      -o /dev/null -w '%{http_code}'
  )
  if [ "$HTTP" -ge 200 ] && [ "$HTTP" -lt 300 ]; then
    printf '%s' "$CANDIDATE"
  else
    printf ''
  fi
}

# -------- A) Snapshot-bound reads --------
divider "A) Snapshot-bound reads"
read ETAG SNAP <<<"$(get_etag_and_snap "$USER_ROLES_CEO")"
echo "ETAG=$ETAG"
echo "SNAP=$SNAP"

echo "# A1) GET enrich (ok)"
curl -sS "$HOST/memory/api/enrich?anchor=$ANCHOR_Q" \
  -H "X-Snapshot-ETag: $SNAP" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: enr-$(rid)" -H "X-Trace-Id: enr-$(tid)" \
  | jq .

echo "# A2) GET enrich (no snapshot → 412)"
curl -isS "$HOST/memory/api/enrich?anchor=$ANCHOR_Q" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: enr-miss-$(rid)" -H "X-Trace-Id: enr-miss-$(tid)" \
  | sed 's/\r$//'

echo "# A3) HEAD with If-None-Match → 304"
curl -isS "$HOST/memory/api/enrich?anchor=$ANCHOR_Q" -X HEAD \
  -H "If-None-Match: \"$ETAG\"" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: head-inm-$(rid)" -H "X-Trace-Id: head-inm-$(tid)" \
  | sed 's/\r$//'

# -------- B) Policy-scoped graph --------
divider "B) Policy-scoped graph (CEO vs Manager)"
SNAP_CEO=$(get_snap ceo)
curl -sS "$HOST/memory/api/graph/expand_candidates" -X POST \
  -H "Content-Type: application/json" \
  -H "X-Snapshot-ETag: $SNAP_CEO" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: exp-ceo-$(rid)" -H "X-Trace-Id: exp-ceo-$(tid)" \
  -d '{"anchor":"'"$ANCHOR_ID"'"}' \
  | jq '{edges:(.graph.edges|length), meta:.meta}'

SNAP_MGR=$(get_snap manager)
curl -sS "$HOST/memory/api/graph/expand_candidates" -X POST \
  -H "Content-Type: application/json" \
  -H "X-Snapshot-ETag: $SNAP_MGR" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: manager" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: exp-mgr-$(rid)" -H "X-Trace-Id: exp-mgr-$(tid)" \
  -d '{"anchor":"'"$ANCHOR_ID"'"}' \
  | jq '{edges:(.graph.edges|length), meta:.meta}'

# -------- C) FP headers --------
divider "C) FP headers"
curl -sS -D - -o /dev/null "$HOST/memory/api/graph/expand_candidates" -X POST \
  -H "Content-Type: application/json" \
  -H "X-Snapshot-ETag: $SNAP_CEO" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: exp-fp-$(rid)" -H "X-Trace-Id: exp-fp-$(tid)" \
  -d '{"anchor":"'"$ANCHOR_ID"'"}' \
  | grep -iE 'x-bv-(graph-fp|policy-fingerprint|allowed-ids-fp|schema-fp)' || true

# -------- D) Batch --------
divider "D) Batch enrich (happy path)"
SNAP_BATCH=$(get_snap ceo)
BODY_HP=$(jq -n --arg anchor "$ANCHOR_ID" --arg snap "$SNAP_BATCH" --arg a "$ANCHOR_ID" --arg b "$ANCHOR_ID_2" \
  '{anchor_id:$anchor, snapshot_etag:$snap, ids:[$a,$b]}')
curl -sS "$HOST/memory/api/enrich/batch" -X POST \
  -H "Content-Type: application/json" \
  -H "X-Snapshot-ETag: $SNAP_BATCH" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: batch-$(rid)" -H "X-Trace-Id: batch-$(tid)" \
  --data-raw "$BODY_HP" | jq .

divider "D2) Batch enrich (fail-closed)"
BODY_DENY=$(jq -n --arg anchor "$ANCHOR_ID" --arg snap "$SNAP_BATCH" --arg a "$ANCHOR_ID" --arg b "eng#d-eng-999" \
  '{anchor_id:$anchor, snapshot_etag:$snap, ids:[$a,$b]}')
curl -isS "$HOST/memory/api/enrich/batch" -X POST \
  -H "Content-Type: application/json" \
  -H "X-Snapshot-ETag: $SNAP_BATCH" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: batch-deny-$(rid)" -H "X-Trace-Id: batch-deny-$(tid)" \
  --data-raw "$BODY_DENY" \
  | sed 's/\r$//'

# -------- E) Event enrich (best-effort) --------
divider "E) Event enrich (best-effort)"
SNAP_WHY=$(get_snap ceo)
ANCHOR_EVENT="${ANCHOR_EVENT:-$(discover_event_anchor || true)}"
if [ -n "$ANCHOR_EVENT" ]; then
  ANCHOR_EVENT_Q="$(printf '%s' "$ANCHOR_EVENT" | sed 's/#/%23/g')"
  curl -sS "$HOST/memory/api/enrich/event?anchor=$ANCHOR_EVENT_Q" \
    -H "X-Snapshot-ETag: $SNAP_WHY" \
    -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
    -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
    -H "X-Request-Id: event-$(rid)" -H "X-Trace-Id: event-$(tid)" \
    | jq .
else
  echo "no resolvable event anchor found → skip"
fi

# -------- F) /v3/query idempotency --------
divider "F) /v3/query idempotency"
IDEM="demo-proof-fixed"
H1=$(curl -sS "$HOST/v3/query?stream=false" -X POST \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: q1-$RUN_REQ_ID" -H "X-Trace-Id: q1-$RUN_REQ_ID" \
  -H "Idempotency-Key: $IDEM" \
  -d '{"anchor_id":"'"$ANCHOR_ID"'","include_event":true}' \
  | (sha256sum 2>/dev/null || shasum -a 256) | cut -d' ' -f1)
H2=$(curl -sS "$HOST/v3/query?stream=false" -X POST \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: ceo" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: q2-$RUN_REQ_ID" -H "X-Trace-Id: q2-$RUN_REQ_ID" \
  -H "Idempotency-Key: $IDEM" \
  -d '{"anchor_id":"'"$ANCHOR_ID"'","include_event":true}' \
  | (sha256sum 2>/dev/null || shasum -a 256) | cut -d' ' -f1)
echo "HASH1=$H1"
echo "HASH2=$H2"

# -------- G) /config (signing key exposure) --------
divider "G) /config (signing key exposure)"
curl -sS "$HOST/config" \
  -H "X-User-Id: $USER_ID" \
  -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" \
  -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: cfg-$(rid)" \
  -H "X-Trace-Id: cfg-$(tid)" \
  | jq -r '.signing.public_key_b64 // "<no-key>"'

# -------- H) /v3/bundles + /v3/verify (all 4 artifacts) --------
divider "H) /v3/bundles (with known-good request id)"
REQ_ID="$FIXED_BUNDLE_REQ_ID"
echo "[bundle] using request_id=$REQ_ID"

# 1) show view bundle (for logs)
BUNDLE_JSON_REQ_ID="bun-view-$(rid)"
curl -sS "$HOST/v3/bundles/$REQ_ID" \
  -H "X-User-Id: $USER_ID" \
  -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" \
  -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: $BUNDLE_JSON_REQ_ID" \
  -H "X-Trace-Id: $BUNDLE_JSON_REQ_ID" \
  | jq .

# 2) pull artifacts separately (these are guaranteed names in your code)
RESP_JSON=$(
  curl -sS "$HOST/v3/bundles/$REQ_ID/response.json" \
    -H "X-Request-Id: bun-resp-$(rid)"
)
REC_JSON=$(
  curl -sS "$HOST/v3/bundles/$REQ_ID/receipt.json" \
    -H "X-Request-Id: bun-rec-$(rid)"
)
TRACE_JSON=$(
  curl -sS "$HOST/v3/bundles/$REQ_ID/trace.json" \
    -H "X-Request-Id: bun-trace-$(rid)"
)
# manifest only comes from the view endpoint, so fetch via JSON bundle
MAN_JSON=$(
  curl -sS "$HOST/v3/bundles/$REQ_ID" \
    -H "X-Request-Id: bun-man-$(rid)" \
  | jq -r '."bundle.manifest.json" // empty'
)

# 3) build verify body – only include what's present
VERIFY_BODY=$(jq -n \
  --argjson response "$RESP_JSON" \
  --argjson receipt  "$REC_JSON" \
  --argjson trace    "$TRACE_JSON" \
  --arg manifest_str "$MAN_JSON" \
  '
  if ($manifest_str | length) > 0 then
    {response:$response,receipt:$receipt,trace:$trace,manifest:($manifest_str|fromjson)}
  else
    {response:$response,receipt:$receipt,trace:$trace}
  end
  ')

VERIFY_REQ_ID="verify-$(rid)"
curl -sS "$HOST/v3/verify" -X POST \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" \
  -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" \
  -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: $VERIFY_REQ_ID" \
  -H "X-Trace-Id: $VERIFY_REQ_ID" \
  --data-raw "$VERIFY_BODY" \
  | jq .

# -------- I) /memory/api/resolve/text --------
divider "I) /memory/api/resolve/text"
RESOLVE_BODY=$(jq -n --arg q "Why did we adopt Stripe for EU billing?" '{q:$q}')

echo "# I1) via gateway (now that proxy forces x-request-id)"
curl -sS "$HOST/memory/api/resolve/text" -X POST \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: resolve-$(rid)" -H "X-Trace-Id: resolve-$(tid)" \
  -d "$RESOLVE_BODY" | jq .

echo "# I2) direct → memory_api (baseline)"
curl -sS "$MEMORY_HOST/api/resolve/text" -X POST \
  -H "Content-Type: application/json" \
  -H "X-User-Id: $USER_ID" -H "X-User-Roles: $USER_ROLES_CEO" \
  -H "X-Policy-Key: $POLICY_KEY" -H "X-Policy-Version: $POLICY_VERSION" \
  -H "X-Request-Id: resolve-direct-$(rid)" -H "X-Trace-Id: resolve-direct-$(tid)" \
  -d "$RESOLVE_BODY" | jq .
