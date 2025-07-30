#!/usr/bin/env bash
set -euo pipefail

# -----------------------------
# Structured logging (deterministic IDs)
# -----------------------------
log(){ # lvl, id, msg
  local lvl="$1"; shift
  local id="$1"; shift
  local msg="$*"
  printf '{"ts":"%s","lvl":"%s","id":"%s","msg":%s}\n' \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$lvl" "$id" "$(jq -Rsa . <<< "$msg")"
}

# IDs
BOOT_ID="BOOTSTRAP-0001"             # script
PYENV_ID="BOOTSTRAP-0100"            # venv
SHADOW_ID="BOOTSTRAP-0200"           # shadow detection
EGGINFO_ID="BOOTSTRAP-0300"          # egg-info cleanup
INSTALL_LOCAL_ID="BOOTSTRAP-0400"    # local pkgs
INSTALL_SVC_ID="BOOTSTRAP-0500"      # services
VERIFY_ID="BOOTSTRAP-0600"           # verification

# Flags
: "${RECREATE_VENV:=0}"     # set to 1 to force new venv
: "${ALLOW_CLEANUP:=0}"     # set to 1 to auto-remove shadowing/egg-info

# -----------------------------
# 0) Venv
# -----------------------------
log INFO "$BOOT_ID" "starting bootstrap"
if [[ "$RECREATE_VENV" == "1" && -d ".venv" ]]; then
  log INFO "$PYENV_ID" "RECREATE_VENV=1: removing existing .venv"
  rm -rf .venv
fi

if [[ ! -d ".venv" ]]; then
  log INFO "$PYENV_ID" "creating venv at .venv"
  python -m venv .venv
fi
. .venv/bin/activate
python -m pip install -U pip >/dev/null
log INFO "$PYENV_ID" "python: $(python --version); pip: $(pip --version)"

# -----------------------------
# 1) Detect name shadowing risks (service roots/tests)
# -----------------------------
shadow_hits=()
while IFS= read -r f; do shadow_hits+=("$f"); done < <(find services -maxdepth 2 -type f -name "__init__.py" | grep -E "/services/[^/]+(/tests)?/__init__\.py$" || true)

if (( ${#shadow_hits[@]} > 0 )); then
  log WARN "$SHADOW_ID" "found potential shadowing __init__.py files:"
  for f in "${shadow_hits[@]}"; do echo " - $f"; done
  if [[ "$ALLOW_CLEANUP" == "1" ]]; then
    for f in "${shadow_hits[@]}"; do rm -f "$f"; done
    log INFO "$SHADOW_ID" "removed shadowing __init__.py files"
  else
    log WARN "$SHADOW_ID" "to auto-remove these next run: ALLOW_CLEANUP=1 ./scripts/dev_bootstrap.sh"
  fi
fi

# -----------------------------
# 2) Remove stale egg-info (in-tree)
# -----------------------------
egginfo_hits=()
while IFS= read -r d; do egginfo_hits+=("$d"); done < <(find services packages -type d -name "*.egg-info" -prune || true)
if (( ${#egginfo_hits[@]} > 0 )); then
  log WARN "$EGGINFO_ID" "found in-repo *.egg-info dirs:"
  for d in "${egginfo_hits[@]}"; do echo " - $d"; done
  if [[ "$ALLOW_CLEANUP" == "1" ]]; then
    for d in "${egginfo_hits[@]}"; do rm -rf "$d"; done
    log INFO "$EGGINFO_ID" "removed *.egg-info dirs"
  else
    log WARN "$EGGINFO_ID" "to auto-remove these next run: ALLOW_CLEANUP=1 ./scripts/dev_bootstrap.sh"
  fi
fi

# -----------------------------
# 3) Install local packages (editables)
#    Order matters for deps seen in your logs
# -----------------------------
install_local(){
  local path="$1"
  if [[ -d "$path" ]]; then
    log INFO "$INSTALL_LOCAL_ID" "pip install -e $path"
    pip install -e "$path" >/dev/null
  else
    log WARN "$INSTALL_LOCAL_ID" "skipping missing local package: $path"
  fi
}

log INFO "$INSTALL_LOCAL_ID" "installing local packages"
install_local packages/core_utils
install_local packages/core_logging
install_local packages/core_config
install_local packages/core_storage   # ok if missing; api_edge may depend on it

# -----------------------------
# 4) Third-party dev/test deps (shared)
# -----------------------------
log INFO "$INSTALL_LOCAL_ID" "installing common dev/test deps"
pip install -q pytest httpx fastapi uvicorn orjson >/dev/null

# -----------------------------
# 5) Install services (editables)
# -----------------------------
install_svc(){
  local path="$1"
  if [[ -f "$path/pyproject.toml" ]]; then
    log INFO "$INSTALL_SVC_ID" "pip install -e $path"
    if ! pip install -e "$path" >/dev/null; then
      log WARN "$INSTALL_SVC_ID" "editable install failed for $path; check local deps and pyproject"
      return 1
    fi
  else
    log WARN "$INSTALL_SVC_ID" "skipping service without pyproject: $path"
  fi
}

install_svc services/gateway      || true
install_svc services/memory_api   || true
install_svc services/ingest       || true
install_svc services/api_edge     || true

# -----------------------------
# 6) Verify importability (fail-fast)
# -----------------------------
verify_import(){
  local mod="$1"
  python - <<PY || { log WARN "$VERIFY_ID" "import failed: $mod"; return 1; }
import importlib, sys
try:
  m = importlib.import_module("$mod")
  print("OK", m.__file__)
except Exception as e:
  print("FAIL", e)
  raise
PY
}

log INFO "$VERIFY_ID" "verifying imports"
verify_import core_utils          || true
verify_import core_logging        || true
verify_import core_config         || true
verify_import core_storage        || true
verify_import gateway             || true
verify_import api_edge            || true
verify_import memory_api          || true
verify_import ingest              || true

log INFO "$BOOT_ID" "bootstrap complete"