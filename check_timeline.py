#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from datetime import datetime

BASE = Path("./memory/fixtures")
DEC_DIR = BASE / "decisions"
EVT_DIR = BASE / "events"
TR_DIR  = BASE / "transitions"

def parse_ts(ts):
    # ISO8601 with Z
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

# Load decisions
decisions = {}
for f in DEC_DIR.glob("*.json"):
    data = json.loads(f.read_text())
    decisions[data["id"]] = parse_ts(data["timestamp"])

# Check events → decisions
errors = []
for f in EVT_DIR.glob("*.json"):
    evt = json.loads(f.read_text())
    e_ts = parse_ts(evt["timestamp"])
    for dec_id in evt.get("led_to", []):
        if dec_id not in decisions:
            errors.append(f"⚠️ Event {evt['id']} refers to missing decision {dec_id}")
        else:
            d_ts = decisions[dec_id]
            if e_ts > d_ts:
                errors.append(
                    f"⏱️  Event {evt['id']} @ {e_ts.isoformat()} **after** Decision {dec_id} @ {d_ts.isoformat()}"
                )

# Check transitions → decisions
for f in TR_DIR.glob("*.json"):
    tr = json.loads(f.read_text())
    tid, frm, to = tr["id"], tr["from"], tr["to"]
    if frm not in decisions or to not in decisions:
        if frm not in decisions:
            errors.append(f"⚠️ Transition {tid} ‘from’ missing decision {frm}")
        if to not in decisions:
            errors.append(f"⚠️ Transition {tid} ‘to’ missing decision {to}")
        continue
    f_ts, t_ts = decisions[frm], decisions[to]
    if f_ts > t_ts:
        errors.append(
            f"⏱️  Transition {tid} links {frm}@{f_ts.isoformat()} → {to}@{t_ts.isoformat()} (order reversed)"
        )

# Summary
if not errors:
    print("✅ All timestamps are chronologically consistent.")
    sys.exit(0)
print("Found timeline inconsistencies:\n")
print("\n".join(errors))
sys.exit(1)
