"""
Monorepo import shim (dev/test only).

Loaded automatically by Python at startup *if* the repo root is on sys.path.
Ensures `packages/*/src` and `services/*/src` are importable so that
`from shared.normalize ...` and similar imports work everywhere.
"""
from pathlib import Path
import sys, os, json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
src_roots = [ROOT] \
    + list((ROOT / "packages").glob("*/src")) \
    + list((ROOT / "services").glob("*/src"))

# Prepend deterministically (preserve order; avoid dups)
for p in map(str, src_roots):
    if p and p not in sys.path:
        sys.path.insert(0, p)

# Optional debug hook (structured line, opt-in)
if os.getenv("BATVAULT_IMPORT_DEBUG") == "1":
    try:
        msg = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "level": "INFO",
            "service": "import-shim",
            "message": "sitecustomize.paths_injected",
            "meta": {
                "paths_added": len(src_roots),
                "first_paths": src_roots[:3],
            },
        }
        print(json.dumps(msg), file=sys.stderr)
    except Exception:
        pass
