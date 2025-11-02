#!/usr/bin/env bash
set -euo pipefail

# Run from repo root regardless of invocation location
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# JSON Schemas → Generated Python (Pydantic) + TypeScript types
# Deterministic output; commit generated artifacts. DO NOT EDIT generated files.

SCHEMAS_DIR="packages/core_models/src/core_models/schemas"
OUT_PY="packages/core_models_gen/src/core_models_gen"
OUT_TS="batvault_frontend/src/types/generated"

if [[ -x "$REPO_ROOT/.tools/venv/bin/python" ]]; then
  PY="$REPO_ROOT/.tools/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "[codegen] ERROR: no python found. Run: bash scripts/bootstrap_min.sh"
  exit 1
fi
echo "[codegen] Using Python at: $PY"

echo "Cleaning known generated targets (precise, not sweeping)…"
# Only remove the exact files we regenerate to avoid clobbering any hand-written files.
declare -a PY_TARGETS=(
  models_memory_meta.py
  models_memory_graph_view.py
  models_bundles_exec_summary.py
  models_bundles_view.py
  models_bundles_trace.py
  models_bundle_manifest.py
  models_receipt.py
  models_edge_wire.py
  models_gateway_plan.py
  models_policy_input.py
  models_policy_decision.py
  models_memory_query_request.py
  models_memory_resolve_response.py
  models_meta_inputs.py
)
for f in "${PY_TARGETS[@]}"; do rm -f "$OUT_PY/$f" || true; done
mkdir -p "$OUT_PY" "$OUT_TS"
declare -a TS_TARGETS=(
  memory.meta.d.ts
  memory.graph_view.d.ts
  bundles.exec_summary.d.ts
  bundles.view.d.ts
  bundles.trace.d.ts
  bundle.manifest.d.ts
  receipt.d.ts
  edge.wire.d.ts
  gateway.plan.d.ts
  policy.input.d.ts
  policy.decision.d.ts
  memory.query.request.d.ts
  memory.resolve.response.d.ts
  meta.inputs.d.ts
)
for f in "${TS_TARGETS[@]}"; do rm -f "$OUT_TS/$f" || true; done

echo "Using checked-in meta.inputs.json (schema-first); not generating from Python..."
# Fail fast to avoid silent drift if the schema isn't present:
if [ ! -f "$SCHEMAS_DIR/meta.inputs.json" ]; then
  echo "[codegen] ERROR: $SCHEMAS_DIR/meta.inputs.json missing. Add it or restore generator." >&2
  exit 1
fi

echo "Generating Python (Pydantic) models from JSON Schemas (directory mode)…"
# 1) Generate ALL schemas into a temp dir so modular $refs work.
TMP_OUT="$OUT_PY/.gen_tmp"
rm -rf "$TMP_OUT"
mkdir -p "$TMP_OUT"
"$PY" -m datamodel_code_generator \
  --input "$SCHEMAS_DIR" \
  --input-file-type jsonschema \
  --target-python-version 3.11 \
  --use-standard-collections \
  --use-title-as-name \
  --disable-timestamp \
  --output-model-type pydantic_v2.BaseModel \
  --output "$TMP_OUT"

# 2) Clean old outputs and rename with safe module names:
#    - prefix with "models_"
#    - replace dots, dashes, and spaces with underscores
#    - skip __init__.py
echo "[codegen:py] Writing sanitized modules → models_*.py"
rm -f "$OUT_PY"/models_*.py
shopt -s nullglob
for f in "$TMP_OUT"/*.py; do
  base="$(basename "$f")"                 # e.g. memory.meta.py
  [[ "$base" == "__init__.py" ]] && continue
  name_no_ext="${base%.py}"               # e.g. memory.meta
  safe="${name_no_ext//./_}"              # -> memory_meta
  safe="${safe//-/_}"                     # dash → _
  safe="${safe// /_}"                     # space → _
  dest="models_${safe}.py"                # -> models_memory_meta.py
  mv "$f" "$OUT_PY/$dest"
done
rm -rf "$TMP_OUT"
rm -rf "$TMP_OUT"

echo "Generating TypeScript types (deterministic style)…"
# Use the Node generator which sets style & additionalProperties consistently.
if command -v node >/dev/null 2>&1; then
  node batvault_frontend/scripts/gen_jsonschema_types.mjs
else
  echo "[codegen] NOTE: 'node' not found; skipping TS type generation."
fi

# Optional: format outputs if tools are present (no-ops if missing).
if command -v ruff >/dev/null 2>&1; then
  ruff format "$OUT_PY"/models_*.py || true
elif command -v black >/dev/null 2>&1; then
  black -q "$OUT_PY"/models_*.py || true
fi
if command -v npx >/dev/null 2>&1; then
  npx --yes prettier -w "$OUT_TS"/*.d.ts || true
fi

echo "Done."