#!/usr/bin/env bash
set -euo pipefail
echo "🔧 Installing BatVault packages & services in editable mode… (services with --no-deps)"

# Core packages
for pkg in core_config core_utils core_logging core_models; do
  pip install --upgrade --editable "packages/${pkg}"
done

# Services
for svc in api_edge gateway memory_api ingest; do
  pip install --upgrade --editable "services/${svc}" --no-deps
done

echo "✅  Done. Run 'pytest' now and imports should resolve."