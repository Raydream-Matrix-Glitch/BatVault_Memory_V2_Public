#!/usr/bin/env bash
set -euo pipefail
: "${GATEWAY_URL:=http://localhost:8080}"
: "${ANCHOR_ID:?set ANCHOR_ID}"

role="${1:-manager}"
ns="${2:-corporate}"
ceil="${3:-high}"

json='{"intent":"why_decision","anchor_id":"'"$ANCHOR_ID"'"}'
resp="$(curl -sS -X POST "$GATEWAY_URL/v2/ask?fresh=1" \
  -H 'Content-Type: application/json' \
  -H "X-User-Roles: $role" \
  -H "X-User-Namespaces: $ns" \
  -H "X-Sensitivity-Ceiling: $ceil" \
  -d "$json")"

if command -v jq >/dev/null 2>&1; then
  echo "$resp" | jq '{events: (.evidence.events|length), policy_trace: .meta.policy_trace, bundle_url}'
else
  echo "$resp" | python - <<'PY'
import sys,json
r=json.load(sys.stdin)
print("events=",len(((r or {}).get("evidence") or {}).get("events") or []))
print("bundle_url=", (r or {}).get("bundle_url"))
print("policy_trace keys=", list(((r or {}).get("meta") or {}).get("policy_trace") or {}).keys())
PY
fi

