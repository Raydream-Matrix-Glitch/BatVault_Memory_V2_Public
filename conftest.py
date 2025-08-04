import sys
from pathlib import Path
import logging, sys, uuid, importlib

# Project root
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))  # guarantee project root is importable
logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger("import-path")


# Dynamically graft every <service|package>/<name>/src onto import path
for pattern in ("services/*/src", "packages/*/src"):
    for candidate in ROOT.glob(pattern):
        full = candidate.resolve()
        if str(full) not in sys.path:
            sys.path.insert(0, p := str(full))
            LOG.debug(
                "import-path-prepend",
                extra={
                    "path": p,
                    "deterministic_id": uuid.uuid5(uuid.NAMESPACE_URL, full.as_uri()).hex,
                },
            )

# ───────────────────────────── namespace shims ───────────────────────────── #
# Some tests import `packages.core_storage.*`.  Create a light-weight        #
# namespace so those imports resolve to the real implementation living in    #
# packages/<name>/src/<name>.                                                #
# -------------------------------------------------------------------------- #
import types as _t
_PKG_ROOT = ROOT / "packages"
pkg_ns = _t.ModuleType("packages")
sys.modules.setdefault("packages", pkg_ns)
for child in _PKG_ROOT.iterdir():
    src = child / "src"
    if src.is_dir():
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            real_mod = importlib.import_module(child.name)
        except ModuleNotFoundError:
            continue
        setattr(pkg_ns, child.name, real_mod)
        sys.modules[f"packages.{child.name}"] = real_mod

# ───────────── absolute fixture path guard (tests expect /mnt/data/memory) ────────── #
_abs_mem = Path("/mnt/data/memory")
try:
    if (ROOT / "memory").exists() and not _abs_mem.exists():
        parent = _abs_mem.parent
        # only symlink if `/mnt/data` (or whatever parent) is present
        if parent.exists():
            _abs_mem.symlink_to(ROOT / "memory", target_is_directory=True)
except (FileExistsError, FileNotFoundError):
    # ignore if either link already exists, or parent/target is missing
    pass