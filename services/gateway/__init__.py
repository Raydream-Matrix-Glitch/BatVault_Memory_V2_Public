"""
Public shim for **Gateway**.

Makes ``from services.gateway import app`` work without altering the existing
*src/* layout (implementation at *services/gateway/src/gateway/app.py*).
"""

from __future__ import annotations

import sys
import importlib
from pathlib import Path
import sys as _sys
import importlib as _imp

_SRC_DIR = Path(__file__).with_name("src")
if _SRC_DIR.is_dir():
    sys.path.insert(0, str(_SRC_DIR))

_gw_mod = importlib.import_module("gateway.app")  # full module, reload-able
app = _gw_mod                                     # type: ignore[assignment]

# --------------------------------------------------------------------- #
#  Alias `services.gateway.evidence` → `gateway.evidence`
#  (needed for timeout tests that import through the *services* shim)

_evidence = _imp.import_module("gateway.evidence")
_sys.modules[__name__ + ".evidence"] = _evidence
setattr(_sys.modules[__name__], "evidence", _evidence)
del _sys, _imp, _evidence, _gw_mod

__all__ = ["app"]

del sys, importlib, Path, _SRC_DIR