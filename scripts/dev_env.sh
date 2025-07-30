#!/usr/bin/env bash
set -euo pipefail

# ---- Structured logging ---------------------------------------------------
BUILD_ID="$(date +%s)-${RANDOM}"
log() { printf '{"event":"dev_env","build_id":"%s","msg":"%s"}\n' "$BUILD_ID" "$1" ; }

# ---- Poetry workspace bootstrap -------------------------------------------
if ! command -v poetry >/dev/null; then
  log "Poetry not found; installing via pipx"
  python -m pip install --quiet pipx
  pipx install poetry
fi

# Point Poetry at Python 3.12 (creates the venv under the hood)
poetry env use 3.12

log "running poetry install"
# Install all workspace packages + dev‑deps, editable
poetry install --with dev --sync >/dev/null

log "🎉  Dev environment finished; to run tests: poetry run pytest -q"
