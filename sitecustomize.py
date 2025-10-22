"""
Monorepo import shim (dev/test only) — **scoped** for Baseline v3.

Loaded automatically by Python at startup *if* the repo root is on sys.path.

Baseline rule: read-time services (Memory/Gateway) MUST NOT import from `shared/*`
(ingest-only). This shim therefore **excludes `packages/shared/src`** unless we are
explicitly running the Ingest process. Set `BATVAULT_INGEST_PROCESS=1` to enable.
See BASELINE_V3.md §6 (Ground rules) and Appendix A.12 (packages/shared).
"""
from pathlib import Path
import sys, os, json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
IS_INGEST = os.getenv("BATVAULT_INGEST_PROCESS") == "1"

def _is_shared_path(p: Path) -> bool:
    """Return True if the path points to packages/shared/src (exact match)."""
    try:
        parts = p.resolve().parts
        # .../packages/shared/src
        return "packages" in parts and "shared" in parts and parts[-1] == "src"
    except Exception:  # path resolution errors only; do not fail import
        return False

# Collect package srcs, excluding shared unless explicitly in ingest mode
_pkg_srcs = list((ROOT / "packages").glob("*/src"))
if not IS_INGEST:
    _pkg_srcs = [p for p in _pkg_srcs if not _is_shared_path(p)]

# Service srcs are fine to expose for dev/test; they don't bypass the Baseline rule
_svc_srcs = list((ROOT / "services").glob("*/src"))

src_roots = [ROOT] + _pkg_srcs + _svc_srcs

# Prepend deterministically (preserve order; avoid dups)
for p in map(str, src_roots):
    if p and p not in sys.path:
        sys.path.insert(0, p)

# Optional debug hook (structured line, opt-in)
if os.getenv("BATVAULT_IMPORT_DEBUG") == "1":
    msg = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
        "level": "INFO",
        "service": "import-shim",
        "message": "sitecustomize.paths_injected",
        "meta": {
            "paths_added": len(src_roots),
            "first_paths": [str(p) for p in src_roots[:3]],
            "ingest_mode": IS_INGEST,
            "shared_included": any(_is_shared_path(Path(p)) for p in src_roots),
        },
    }
    print(json.dumps(msg), file=sys.stderr)
