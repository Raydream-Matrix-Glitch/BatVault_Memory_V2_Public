#!/usr/bin/env bash
set -euo pipefail

python -m pip install -U pip wheel setuptools

# 1) External runtime deps
pip install -r requirements/runtime.txt

# 2) Install all first‑party packages & services
for path in packages/* services/*; do
  [ -f "$path/pyproject.toml" ] && pip install -e "$path"
done

# 3) Dev tooling (pytest, etc.)
pip install -r requirements/dev.txt
