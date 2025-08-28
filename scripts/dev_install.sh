#!/usr/bin/env bash
# One‑shot developer setup for running tests locally.
# - Creates/uses .venv unless NO_VENV=1
# - Installs runtime + dev deps
# - Installs first‑party packages/services in editable mode
# - Verifies key imports
# - Optional: RUN_TESTS=1 will run `pytest -q`
set -euo pipefail

# -----------------------------
# Structured logging (deterministic IDs)
# -----------------------------
log(){ # lvl, id, msg, k=v...
  local lvl="$1"; shift
  local id="$1"; shift
  local msg="$1"; shift || true
  local ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  local extras=""
  for kv in "$@"; do
    extras+=",\"${kv%%=*}\":\"${kv#*=}\""
  done
  printf '{"ts":"%s","lvl":"%s","id":"%s","msg":"%s"%s}\n' \
    "$ts" "$lvl" "$id" "$msg" "$extras"
}

INSTALL_ID="DEV-INSTALL-0001"
trap 'log ERROR "$INSTALL_ID" "unexpected error" step="$STEP" exit="$?"' ERR

STEP="detect_python"
PY_BIN="${PY_BIN:-python3}"
PIP_BIN="${PIP_BIN:-pip}"

# -----------------------------
# Virtualenv (opt‑out via NO_VENV=1)
# -----------------------------
STEP="venv"
if [[ "${NO_VENV:-0}" != "1" ]]; then
  if [[ ! -d ".venv" ]]; then
    log INFO "$INSTALL_ID" "creating virtualenv"
    "$PY_BIN" -m venv .venv
  fi
  # shellcheck disable=SC1091
  . .venv/bin/activate
  PIP_BIN="python -m pip"
  log INFO "$INSTALL_ID" "venv ready" python="$(python -V)"
else
  log INFO "$INSTALL_ID" "NO_VENV=1: using system interpreter" python="$($PY_BIN -V 2>&1)"
fi

# -----------------------------
# Retry helper with jitter
# -----------------------------
retry(){ # n cmd...
  local n="$1"; shift
  local attempt=1
  until "$@"; do
    if (( attempt >= n )); then return 1; fi
    local sleep_for=$(( (RANDOM % 400 + 200) / 100 )) # 2.0‑6.0s jitter
    log WARN "$INSTALL_ID" "retrying" attempt="$attempt" delay="${sleep_for}s" cmd="$*"
    sleep "$sleep_for"
    ((attempt++))
  done
}

# -----------------------------
# Base tools
# -----------------------------
STEP="pip_tools"
retry 3 bash -lc "$PIP_BIN install -U pip wheel setuptools"

# -----------------------------
# External dependencies
# -----------------------------
STEP="requirements_runtime"
if [[ -f "requirements/runtime.txt" ]]; then
  retry 3 bash -lc "$PIP_BIN install -r requirements/runtime.txt"
fi
STEP="requirements_dev"
if [[ -f "requirements/dev.txt" ]]; then
  retry 3 bash -lc "$PIP_BIN install -r requirements/dev.txt"
else
  # minimal test deps
  retry 3 bash -lc "$PIP_BIN install pytest"
fi

# -----------------------------
# First‑party packages/services (editable)
# -----------------------------
STEP="install_editable"
for pkg in core_config core_logging core_models core_metrics core_utils core_storage core_validator link_utils shared; do
  if [[ -d "packages/$pkg" ]]; then
    log INFO "$INSTALL_ID" "installing package" name="$pkg"
    retry 3 bash -lc "$PIP_BIN install -e packages/$pkg"
  fi
done
for svc in api_edge gateway memory_api ingest; do
  if [[ -d "services/$svc" ]]; then
    log INFO "$INSTALL_ID" "installing service" name="$svc"
    retry 3 bash -lc "$PIP_BIN install -e services/$svc"
  fi
done

# -----------------------------
# Import verification (deterministic, cached)
# -----------------------------
STEP="verify_imports"
python - <<'PY'
import importlib, sys
mods = [
  "core_utils","core_logging","core_config","core_storage",
  "shared",
  "api_edge","gateway","memory_api","ingest",
]
failed = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append((m, repr(e)))
if failed:
    for m, e in failed:
        print(f'{{"lvl":"ERROR","id":"DEV-INSTALL-0001","msg":"import failed","module":"{m}","error":{e!r}}}')
    sys.exit(1)
print('{"lvl":"INFO","id":"DEV-INSTALL-0001","msg":"imports ok"}')
PY

# -----------------------------
# Optional: run tests
# -----------------------------
if [[ "${RUN_TESTS:-0}" == "1" ]]; then
  # 1) Run Memory-API’s own tests (so its conftest.py autouse fixture kicks in)
  STEP="pytest-memory_api"
  log INFO "$INSTALL_ID" "running Memory-API contract tests"
  pytest -q services/memory_api/tests

  # 2) Run the rest of the suite (skip Memory-API tests to avoid re-running)
  STEP="pytest-rest"
  log INFO "$INSTALL_ID" "running remaining tests"
  pytest -q --ignore=services/memory_api/tests
fi

log INFO "$INSTALL_ID" "ready for tests" hint="activate venv and run: pytest -q"