#!/usr/bin/env bash
set -euo pipefail

# Minimal, auditable bootstrap with NO pyproject installs.
# - Creates a small venv for codegen + receipt verify
# - Installs only the required Python & Node tools
# - Runs codegen
# - Optional: verifies a receipt via scripts/verify_receipt.py

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
NODE_BIN="${NODE_BIN:-node}"
NPM_BIN="${NPM_BIN:-npm}"

VENV_DIR="$REPO_ROOT/.tools/venv"
PATH="$VENV_DIR/bin:$PATH"

# --- 1) Python venv with strictly necessary tools ---
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[bootstrap] Creating venv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade --quiet pip

# datamodel-code-generator for Pydantic v2 output; PyNaCl for receipt verification CLI
# (Pin conservatively; adjust versions here only if you need to.)
python -m pip install --quiet \
  "datamodel-code-generator==0.25.4" \
  "pydantic>=2.6,<3" \
  "PyNaCl>=1.5,<2"

# Ensure repo root is on sys.path so sitecustomize.py wires /packages/*/src
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# --- 2) Node deps strictly for TS type generation ---
# json-schema-to-typescript imports in the FE script; install just what it needs locally
if command -v "$NPM_BIN" >/dev/null 2>&1; then
  echo "[bootstrap] Installing minimal Node deps for TS typegen…"
  "$NPM_BIN" --prefix "$REPO_ROOT/batvault_frontend" \
    --no-audit --no-fund --silent \
    install json-schema-to-typescript@13.1.1 typescript@5.4.5 prettier@2.8.8
else
  echo "[bootstrap] WARNING: npm not found; skipping TS type generation."
fi

# --- 3) Run your existing codegen (now that tools exist) ---
echo "[bootstrap] Running codegen_schemas.sh …"
bash "$REPO_ROOT/scripts/codegen_schemas.sh"

# --- 4) Optional: verify a receipt (CLI) ---
if [[ "${1:-}" == "--verify" ]]; then
  shift
  if [[ $# -lt 2 ]]; then
    echo "Usage: $0 --verify /path/response.json /path/receipt.json [--pubkey path]"
    exit 2
  fi
  RESP="$1"; REC="$2"; shift 2
  PUBARG=()
  if [[ "${1:-}" == "--pubkey" ]]; then
    PUBARG=(--pubkey "$2")
  fi
  echo "[bootstrap] Verifying receipt…"
  python "$REPO_ROOT/scripts/verify_receipt.py" "$RESP" "$REC" "${PUBARG[@]}"
fi

echo "[bootstrap] Done."
