import sys, json, os, glob, re
from core_logging import get_logger, log_stage
from core_utils import compute_snapshot_etag_for_files

logger = get_logger("ingest-cli")
ID_RE = re.compile(r"^[a-z0-9_\-:.]{3,128}$")

def validate_doc(doc: dict, path: str) -> list[str]:
    errs: list[str] = []
    if "id" not in doc or not isinstance(doc["id"], str) or not ID_RE.match(doc["id"]):
        errs.append("invalid id")
    ts = doc.get("timestamp") or doc.get("ts") or doc.get("updated_at")
    if ts is None:
        errs.append("missing timestamp (ISO-8601 expected)")
    content = doc.get("content") or doc.get("title") or doc.get("text")
    if not content:
        errs.append("missing content field (title/text)")
    return errs

def seed(path: str) -> int:
    files = [p for p in glob.glob(os.path.join(path, "*.json"))]
    if not files:
        print("No JSON files found.", file=sys.stderr)
        return 1
    all_errs: list[str] = []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as e:
            all_errs.append(f"{p}: json error {e}")
            continue
        errs = validate_doc(doc, p)
        if errs:
            all_errs.extend([f"{p}: {e}" for e in errs])
    if all_errs:
        for e in all_errs:
            print(e, file=sys.stderr)
        return 2

    snapshot_etag = compute_snapshot_etag_for_files(files)
    log_stage(logger, "ingest", "seed_validation_ok", snapshot_etag=snapshot_etag, files=len(files))
    print(json.dumps({"ok": True, "files": len(files), "snapshot_etag": snapshot_etag}))
    return 0

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "seed":
        print("usage: python -m ingest.cli seed <dir>", file=sys.stderr)
        sys.exit(1)
    sys.exit(seed(sys.argv[2]))

if __name__ == "__main__":
    main()
