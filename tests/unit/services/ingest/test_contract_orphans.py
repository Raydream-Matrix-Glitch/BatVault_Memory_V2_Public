import json, pathlib
from jsonschema import Draft202012Validator as V
from ingest.cli import load_schema


def _events_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        cand = parent / "memory" / "fixtures" / "events"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("memory/fixtures/events directory not found")

ROOT = _events_root()


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def test_orphan_events_validate_and_detect():
    """
    Orphan = event with led_to missing or [].  The pipeline must accept them
    (spec §6.2) and they must validate against the Event schema.
    """
    events = [_load(p) for p in ROOT.glob("*.json")]
    orphans = [e for e in events if not e.get("led_to")]

    # We purposefully added exactly two orphan fixtures.
    ids = {e["id"] for e in orphans}
    assert {"phil-e11", "pan-e10"} <= ids

    schema = load_schema("event")
    for evt in orphans:
        V(schema).validate(evt)          # must pass strict schema check
        assert evt.get("led_to", []) == []  # explicit orphan