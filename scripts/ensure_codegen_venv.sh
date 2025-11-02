#!/usr/bin/env bash
set -euo pipefail

# Ensures a minimal venv for OpenAPI/codegen and runs dump_openapi.py inside it.
# It parses pyproject.toml for the *services* and core libs, filters out monorepo
# packages (core_*, shared, etc.), installs only third-party deps, then dumps specs.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.tools/venv"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[codegen-venv] creating $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --quiet --upgrade pip setuptools wheel

# Ensure 'tomllib' exists on Python <3.11
python - <<'PY'
try:
    import tomllib  # py311+
except ModuleNotFoundError:
    import sys, subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "tomli>=2"])
PY

# Build a minimal requirements list by reading pyproject.toml files and
# keeping only *third-party* packages (drop core_* & other in-repo names).
REQ_FILE="$(mktemp)"
python - "$REPO_ROOT" > "$REQ_FILE" <<'PY'
import sys, re
try:
    import tomllib  # py311+
except ModuleNotFoundError:
    import tomli as tomllib  # py310 fallback
from pathlib import Path

root = Path(sys.argv[1])
# pyprojects to scan (services + the core libs most likely imported by them)
files = [
    root / "services" / "gateway" / "pyproject.toml",
    root / "services" / "memory_api" / "pyproject.toml",
    root / "packages" / "core_validator" / "pyproject.toml",
    root / "packages" / "core_models" / "pyproject.toml",
    root / "packages" / "core_http" / "pyproject.toml",
    root / "packages" / "core_observability" / "pyproject.toml",
    root / "packages" / "core_logging" / "pyproject.toml",
    root / "packages" / "core_utils" / "pyproject.toml",
    root / "packages" / "core_storage" / "pyproject.toml",
    root / "packages" / "core_metrics" / "pyproject.toml",
]
third_party: set[str] = set()
drop_prefixes = ("core_", "gateway", "memory_api", "shared")
def keep(dep: str) -> bool:
    # Extract the plain package name (strip version/extras/markers)
    name = dep
    name = re.split(r"[<>=!; ]", name, maxsplit=1)[0]
    name = name.split("[",1)[0]
    return not name.startswith(drop_prefixes)

for f in files:
    if not f.exists(): 
        continue
    raw = f.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        import sys, re
        dups = sum(1 for line in raw.splitlines() if line.strip() == "[project.optional-dependencies]")
        print(f"[codegen-venv] Invalid TOML in {f}: {e}", file=sys.stderr)
        if dups > 1:
            print("  Hint: merge repeated [project.optional-dependencies] blocks into a single table.", file=sys.stderr)
        sys.exit(2)
    for dep in data.get("project",{}).get("dependencies",[]) or []:
        if keep(dep):
            third_party.add(dep)

# A tiny safety net: these are frequently needed even if not listed.
must_haves = {
    "httpx>=0.27.0",
    "fastapi>=0.112.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "jsonschema>=4.21.1,<5",
}
third_party |= must_haves

print("\n".join(sorted(third_party)))
PY

echo "[codegen-venv] installing third-party deps for codegen…"
# 1) Always install hard must-haves that the dumper/import chain needs.
python -m pip install --quiet \
  "httpx>=0.27.0" \
  "fastapi>=0.112.0" \
  "pydantic>=2.7.0" \
  "pydantic-settings>=2.3.0" \
  "jsonschema>=4.21.1,<5" \
  "datamodel-code-generator>=0.25.6" \
  "orjson>=3.9.7" \
  "redis>=5.0.3" \
  "starlette>=0.37.2"

# 1.5) Install the project's runtime requirements (contains httpx)
python -m pip install --quiet -r "$REPO_ROOT/requirements/runtime.txt"

# 1.5) Install the project's runtime requirements (contains httpx)
python -m pip install --quiet -r "$REPO_ROOT/requirements/runtime.txt"

# 2) Then install anything else discovered from pyprojects (safe if duplicates).
python -m pip install --quiet -r "$REQ_FILE"

# Make sure our repo root is on sys.path so `sitecustomize.py` wires /packages/*/src
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Now generate OpenAPI snapshots locally for the FE to consume.
python "$REPO_ROOT/scripts/dump_openapi.py"
echo "[codegen-venv] OpenAPI dumped to batvault_frontend/openapi/"
