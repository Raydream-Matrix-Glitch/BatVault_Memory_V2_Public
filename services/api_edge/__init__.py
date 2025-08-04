"""
Public shim for **API Edge**.

Makes ``from services.api_edge import app`` work while we keep the
component’s real implementation in *services/api_edge/src/api_edge/*.
"""

from __future__ import annotations

import sys
import importlib
from pathlib import Path

# 1) Add the internal *src* directory to the import path (idempotent).
_SRC_DIR = Path(__file__).with_name("src")
if _SRC_DIR.is_dir():
    sys.path.insert(0, str(_SRC_DIR))

_mod = importlib.import_module("api_edge.app")
# expose the **module object** so tests can `importlib.reload()` it
app = _mod  # type: ignore[assignment]

# ── backwards-compat: add `iter_content()` to httpx.Response ──────────
import httpx
if not hasattr(httpx.Response, "iter_content"):
    def _iter_content(self: "httpx.Response", chunk_size: int = 8192):
        yield from self.iter_bytes(chunk_size)
    httpx.Response.iter_content = _iter_content        # type: ignore[attr-defined]

__all__ = ["app"]

del sys, importlib, Path, _SRC_DIR