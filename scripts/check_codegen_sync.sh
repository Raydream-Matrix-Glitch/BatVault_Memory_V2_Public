#!/usr/bin/env bash
set -euo pipefail

SCHEMAS_DIR="packages/core_models/src/core_models/schemas"
OUT_PY="packages/core_models_gen/src/core_models_gen"
OUT_TS="batvault_frontend/src/types/generated"

pre_hash="$(git ls-files -s -- "$OUT_PY" "$OUT_TS" 2>/dev/null || true)"

./scripts/codegen_schemas.sh

post_changes="$(git status --porcelain -- "$OUT_PY" "$OUT_TS" | wc -l | tr -d ' ')"
if [[ -z "$pre_hash" ]]; then
  # No baseline committed yet; warn but don't fail (will become strict once artifacts are committed).
  printf '{"ts":"%s","event":"codegen_artifacts_missing","msg":"Artifacts missing from repo; skipping strict drift check for now"}\\n' \
    "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  exit 0
fi

if [[ "$post_changes" != "0" ]]; then
  echo "ERROR: Generated files are out of date. Run scripts/codegen_schemas.sh and commit the changes." >&2
  git --no-pager diff --stat -- "$OUT_PY" "$OUT_TS" || true
  exit 1
fi
echo "Codegen in sync."