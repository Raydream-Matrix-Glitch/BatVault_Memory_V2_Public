#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/batvault_frontend"

# Guarantees Python codegen deps, dumps OpenAPI, runs all FE codegen + build.
bash ../scripts/ensure_codegen_venv.sh
npm run codegen:regex
npm run codegen:types
npm run codegen:sdk
npm run codegen:hooks
npm run env:sync
npm run build
