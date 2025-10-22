#!/usr/bin/env bash
set -euo pipefail

# Run from repo root regardless of invocation location
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# JSON Schemas → Generated Python (Pydantic) + TypeScript types (deterministic; commit artifacts)
# Single-source JSON Schemas → Generated Python (Pydantic) + TypeScript types
# Deterministic output; commit generated artifacts. DO NOT EDIT generated files.

SCHEMAS_DIR="packages/core_models/src/core_models/schemas"
OUT_PY="packages/core_models_gen/src/core_models_gen"
OUT_TS="batvault_frontend/src/types/generated"

echo "Cleaning generated dirs..."
rm -rf "$OUT_PY" "$OUT_TS"
mkdir -p "$OUT_PY" "$OUT_TS"

echo "Generating Python models (Pydantic v2) from JSON Schemas..."
# Resolve Python interpreter (prefer repo venv, then python3, then python).
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Error: Python 3 not found on PATH (need python3 or python)" >&2
  exit 127
fi

# Ensure pip exists (some distros ship Python without pip)
if ! $PY -c "import pip" >/dev/null 2>&1; then
  echo "Python is present but pip is missing." >&2
  echo "Bootstrap pip and install runtime deps:" >&2
  echo "  $PY -m ensurepip --upgrade && $PY -m pip install -r requirements/runtime.txt" >&2
  exit 127
fi

# Ensure the generator is installed (pinned in runtime requirements)
if ! $PY -c "import datamodel_code_generator" >/dev/null 2>&1; then
  echo "Missing dependency: 'datamodel-code-generator' (pinned in requirements/runtime.txt)." >&2
  echo "Install with:" >&2
  echo "  $PY -m pip install -r requirements/runtime.txt" >&2
  exit 127
fi

# Common flags for deterministic output
DCG_COMMON=(--input-file-type jsonschema --target-python-version 3.11 --use-standard-collections --disable-timestamp --wrap-string-literal)
DCG_V2=(--output-model-type pydantic_v2.BaseModel)

$PY -m datamodel_code_generator \
  --input "$SCHEMAS_DIR/bundles.exec_summary.json" \
  "${DCG_COMMON[@]}" \
  "${DCG_V2[@]}" \
  --output "$OUT_PY/bundles_exec_summary"

# (defer package __init__ creation until all generators have run)

# Graph view (edges-only)
$PY -m datamodel_code_generator \
  --input "$SCHEMAS_DIR/memory.graph_view.json" \
  "${DCG_COMMON[@]}" \
  "${DCG_V2[@]}" \
  --output "$OUT_PY/memory_graph_view"

# Add __init__.py inside each generated subpackage so star-imports work reliably
for pkg in bundles_exec_summary memory_graph_view; do
  pkg_dir="$OUT_PY/$pkg"
  if [ -d "$pkg_dir" ]; then
    : > "$pkg_dir/__init__.py"
    for f in "$pkg_dir"/*.py; do
      bn="$(basename "$f")"; [ "$bn" = "__init__.py" ] && continue
      mod="${bn%.py}"
      echo "from .${mod} import *" >> "$pkg_dir/__init__.py"
    done
  fi
done

# Aggregate re-exports at the package root (keeps your import surface stable)
cat > "$OUT_PY/__init__.py" <<'PY'
from .bundles_exec_summary import *  # re-export generated models
from .memory_graph_view import *     # re-export generated models
PY

# (Optional) Decision node model – useful if we later alias WhyDecisionAnchor to a generated class
# Uncomment if you want a generated anchor class now.
# datamodel-code-generator \
#   --input "$SCHEMAS_DIR/decision.json" \
#   --input-file-type jsonschema \
#   --target-python-version 3.11 \
#   --use-standard-collections \
#   --disable-timestamp \
#   --output "$OUT_PY/models_decision.py"

echo "Generating TypeScript types from JSON Schemas..."
# json-schema-to-typescript resolves relative $ref against process.cwd.
# Run it inside the schemas directory so refs like './edge.wire.json' resolve correctly.
(
  cd "$SCHEMAS_DIR"
  npx --yes json-schema-to-typescript memory.meta.json          > "$REPO_ROOT/$OUT_TS/memory.meta.d.ts"
  npx --yes json-schema-to-typescript memory.graph_view.json    > "$REPO_ROOT/$OUT_TS/memory.graph_view.d.ts"
  npx --yes json-schema-to-typescript bundles.exec_summary.json > "$REPO_ROOT/$OUT_TS/bundles.exec_summary.d.ts"
)

echo "Done."