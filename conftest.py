import sys
from pathlib import Path
import logging, sys, uuid

# Project root
ROOT = Path(__file__).parent.resolve()
logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger("import-path")


# Dynamically graft every `<something>/src` folder onto sys.path
for candidate in ROOT.glob("{services,packages}/**/src"):
    full = candidate.resolve()
    sys.path.insert(0, p := str(full))
    LOG.debug(
        "import-path-prepend",
        extra={
            "path": p,
            "deterministic_id": uuid.uuid5(uuid.NAMESPACE_URL, p.as_uri()).hex,
        },
    )