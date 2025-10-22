from __future__ import annotations

import sys
from pathlib import Path

def ensure_monorepo_paths() -> None:
    """Ensure /app (repo root) is on sys.path and import sitecustomize.

    This makes 'packages/*/src' and 'services/*/src' importable uniformly
    across all entrypoints without duplicating shim code.
    """
    # packages/core_utils/src/core_utils/bootstrap.py -> /app
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import sitecustomize  # noqa: F401
    except ImportError:
        # If missing in some environments, continue; imports will still work via root addition.
        pass