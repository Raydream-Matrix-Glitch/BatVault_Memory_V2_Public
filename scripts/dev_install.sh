#!/usr/bin/env bash
set -euo pipefail

echo "🔧 Installing BatVault packages & services in editable mode…"

# Core packages
for pkg in core_config core_utils core_logging core_models; do
  pip install --upgrade --editable "packages/${pkg}/src/${pkg}"
done

# Services
for svc in api_edge gateway memory_api ingest; do
  pip install --upgrade --editable "services/${svc}/src/${svc}"
done

echo "✅  Done. Run 'pytest' now and imports should resolve."