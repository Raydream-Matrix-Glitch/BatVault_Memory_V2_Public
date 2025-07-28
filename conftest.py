# conftest.py
import logging, sys
from pathlib import Path

LOG = logging.getLogger("import-path")
for p in Path(__file__).parent.glob("*/*/src"):
    sys.path.append(s := str(p))
    LOG.debug("added‑to‑sys‑path", extra={"path": s, "event_id": "import_path_append"})