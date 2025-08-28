"""
Monorepo import shim (dev/test only).

Loaded automatically by Python at startup *if* the repo root is on sys.path.
Ensures `packages/*/src` and `services/*/src` are importable so that
`from shared.normalize ...` and similar imports work everywhere.
"""
from pathlib import Path
import sys, os

ROOT = Path(__file__).resolve().parent
src_roots = [ROOT] \
    + list((ROOT / "packages").glob("*/src")) \
    + list((ROOT / "services").glob("*/src"))

# Prepend deterministically (preserve order; avoid dups)
for p in map(str, src_roots):
    if p and p not in sys.path:
        sys.path.insert(0, p)

# Optional debug hook (no-op unless explicitly enabled)
if os.getenv("BATVAULT_IMPORT_DEBUG") == "1":
    try:
        from datetime import datetime
        print(f"[batvault-sitecustomize] {datetime.utcnow().isoformat()} "
              f"added {len(src_roots)} paths", file=sys.stderr)
    except Exception:
        pass
