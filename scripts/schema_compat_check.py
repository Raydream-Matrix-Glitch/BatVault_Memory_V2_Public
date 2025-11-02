#!/usr/bin/env python3
import json, sys, subprocess, os, pathlib, time
from typing import Any, Dict, List, Optional

SCHEMAS_DIR = pathlib.Path("packages/core_models/src/core_models/schemas")

def _load_json(p: pathlib.Path) -> Dict[str, Any]:
    return json.loads(p.read_text())

def _git_show(path: pathlib.Path, ref: str) -> Optional[Dict[str, Any]]:
    try:
        out = subprocess.check_output(["git", "show", f"{ref}:{path.as_posix()}"],
                                      stderr=subprocess.DEVNULL)
        return json.loads(out.decode("utf-8"))
    except Exception:
        return None

def _enum_set(d: Dict[str, Any]) -> Optional[set]:
    v = d.get("enum")
    return set(v) if isinstance(v, list) else None

def _required_set(d: Dict[str, Any]) -> Optional[set]:
    v = d.get("required")
    return set(v) if isinstance(v, list) else None

def _pattern(d: Dict[str, Any]) -> Optional[str]:
    return d.get("pattern") if isinstance(d.get("pattern"), str) else None

def _flatten_props(schema: Dict[str, Any], path="$") -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(schema, dict):
        return out
    if "properties" in schema and isinstance(schema["properties"], dict):
        for k, v in schema["properties"].items():
            out[f"{path}.{k}"] = v if isinstance(v, dict) else {}
            out.update(_flatten_props(v, f"{path}.{k}"))
    return out

def _log(event: str, **attrs):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "service":"ci", "stage":"schema_compat", "event":event}
    rec.update(attrs)
    print(json.dumps(rec))

def check_file(path: pathlib.Path, base_ref: str) -> int:
    cur = _load_json(path)
    prev = _git_show(path, base_ref)
    if prev is None:
        _log("no_base_version", file=str(path), base_ref=base_ref)
        return 0

    status = 0
    # Required fields
    cur_req = _required_set(cur) or set()
    prev_req = _required_set(prev) or set()
    added_req = cur_req - prev_req
    if added_req:
        _log("breaking_required_added", file=str(path), added=sorted(added_req))
        status = 1

    # Compare nested properties
    cur_props = _flatten_props(cur)
    prev_props = _flatten_props(prev)

    for key, prev_desc in prev_props.items():
        cur_desc = cur_props.get(key, {})
        # Type changes
        if "type" in prev_desc and "type" in cur_desc and prev_desc["type"] != cur_desc["type"]:
            _log("breaking_type_changed", file=str(path), key=key,
                 before=prev_desc["type"], after=cur_desc["type"])
            status = 1
        # Enum removals
        prev_enum = _enum_set(prev_desc)
        cur_enum = _enum_set(cur_desc)
        if prev_enum and cur_enum and not prev_enum.issubset(cur_enum):
            _log("breaking_enum_values_removed", file=str(path), key=key,
                 removed=sorted(prev_enum - cur_enum))
            status = 1
        # Pattern changes (heuristic): longer pattern → likely narrower → breaking
        prev_pat, cur_pat = _pattern(prev_desc), _pattern(cur_desc)
        if prev_pat and cur_pat and prev_pat != cur_pat and len(cur_pat) >= len(prev_pat):
            _log("breaking_pattern_narrowed_or_changed", file=str(path), key=key,
                 before=prev_pat, after=cur_pat)
            status = 1

    # Property removals
    for key in prev_props:
        if key not in cur_props:
            _log("breaking_property_removed", file=str(path), key=key)
            status = 1

    if status == 0:
        _log("compat_ok", file=str(path))
    return status

def main(argv: List[str]) -> int:
    base_ref = os.environ.get("SCHEMA_BASE_REF") or "origin/main"
    try:
        subprocess.check_call(["git", "rev-parse", "--verify", base_ref],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        base_ref = "HEAD~1"
    status = 0
    for p in sorted(SCHEMAS_DIR.glob("*.json")):
        status |= check_file(p, base_ref)
    return status

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))