import sys, json, os, glob, re, time
from jsonschema import Draft202012Validator
from core_logging import get_logger, log_stage
from core_utils import compute_snapshot_etag_for_files, slugify_id
from core_storage import ArangoStore
from core_config import get_settings
from .pipeline.normalize import normalize_decision, normalize_event, normalize_transition, derive_backlinks
from .pipeline.graph_upsert import upsert_all
from .catalog.field_catalog import build_field_catalog, build_relation_catalog

logger = get_logger("ingest-cli")
settings = get_settings()

# ---------- Alias map (extend as needed) ----------
ALIASES = {
    "id": ["id", "_id", "key"],
    "timestamp": ["timestamp", "ts", "updated_at"],
    "option": ["option", "title", "decision", "choice"],
    "rationale": ["rationale", "why", "reasoning"],
    "summary": ["summary", "headline", "title"],
    "description": ["description", "content", "text", "body"],
    "supported_by": ["supported_by", "evidence", "events"],
    "based_on": ["based_on", "basedOn", "sources"],
    "transitions": ["transitions", "links"],
    "led_to": ["led_to", "leads_to", "ledTo"],
    "from": ["from", "src", "source"],
    "to": ["to", "dst", "target"],
    "relation": ["relation", "rel", "type"],
    "tags": ["tags", "labels"],
}

def pick_alias(doc: dict, key: str):
    for k in ALIASES.get(key, [key]):
        if k in doc:
            return doc[k], k
    return None, None

def canonicalize(doc: dict) -> tuple[dict, dict]:
    """
    Returns (canon_doc, alias_hits) where canon_doc has canonical keys populated
    from the first alias present; original fields are retained.
    """
    out = dict(doc)
    hits: dict[str, str] = {}
    for k in ALIASES.keys():
        if k in out:
            continue
        val, used = pick_alias(doc, k)
        if used is not None:
            out[k] = val
            hits[k] = used
    return out, hits

def load_schema(name: str) -> dict:
    base = os.path.join(os.path.dirname(__file__), "..", "schemas", "json-v2")
    with open(os.path.join(base, f"{name}.schema.json"), "r", encoding="utf-8") as f:
        return json.load(f)

SC_DECISION = load_schema("decision")
SC_EVENT = load_schema("event")
SC_TRANSITION = load_schema("transition")

def validate_doc(doc: dict, kind: str, path: str) -> list[str]:
    sch = {"decision": SC_DECISION, "event": SC_EVENT, "transition": SC_TRANSITION}[kind]
    v = Draft202012Validator(sch)
    errs = [f"{path}: {e.message}" for e in sorted(v.iter_errors(doc), key=lambda e: e.path)]
    return errs

def seed(path: str) -> int:
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    if not files:
        print("No JSON files found.", file=sys.stderr); return 1

    raw_decisions, raw_events, raw_transitions = {}, {}, {}
    errors = []
    alias_stats = {"hits": 0, "docs": 0}
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as e:
            errors.append(f"{p}: json error {e}")
            continue
        # Canonicalize aliases BEFORE kind inference and schema validation
        doc, hits = canonicalize(doc)
        alias_stats["docs"] += 1
        alias_stats["hits"] += len(hits)
        # Alias-aware kind inference
        kind = doc.get("kind") or (
            "transition" if all(k in doc for k in ("from","to","relation"))
            else "decision" if "option" in doc
            else "event" if ("summary" in doc or "description" in doc)
            else None
        )
        if kind not in ("decision","event","transition"):
            errors.append(f"{p}: cannot infer kind (expected decision/event/transition)"); continue
        # Validate canonicalized doc against v2 schema
        errors.extend(validate_doc(doc, kind, p))
        if kind == "decision": raw_decisions[doc["id"]] = doc
        elif kind == "event": raw_events[doc["id"]] = doc
        else: raw_transitions[doc["id"]] = doc

    if errors:
        for e in errors: print(e, file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    log_stage(logger, "ingest", "pre_normalization_snapshot",
              files=len(files), alias_docs=alias_stats["docs"], alias_hits=alias_stats["hits"])

    # Normalize
    decisions = {k: normalize_decision(v) for k, v in raw_decisions.items()}
    events = {k: normalize_event(v) for k, v in raw_events.items()}
    transitions = {k: normalize_transition(v) for k, v in raw_transitions.items()}

    # Derivations (backlinks & transition listing)
    derive_backlinks(decisions, events, transitions)

    # Referential integrity checks (fail fast with clear messages)
    ri_errors = []
    for e in events.values():
        for did in e.get("led_to", []):
            if did not in decisions:
                ri_errors.append(f"event {e['id']} -> missing decision '{did}'")
    for t in transitions.values():
        if t.get("from") not in decisions:
            ri_errors.append(f"transition {t['id']} from missing decision '{t.get('from')}'")
        if t.get("to") not in decisions:
            ri_errors.append(f"transition {t['id']} to missing decision '{t.get('to')}'")
    if ri_errors:
        for m in ri_errors: print(m, file=sys.stderr)
        return 3

    snapshot_etag = compute_snapshot_etag_for_files(files)
    dt_ms = int((time.perf_counter() - t0) * 1000)
    log_stage(logger, "ingest", "post_normalization_snapshot",
              snapshot_etag=snapshot_etag,
              decisions=len(decisions), events=len(events), transitions=len(transitions),
              latency_ms=dt_ms)

    # Upsert to Arango
    store = ArangoStore(settings.arango_url, settings.arango_root_user, settings.arango_root_password,
                        settings.arango_db, settings.arango_graph_name,
                        settings.arango_catalog_collection, settings.arango_meta_collection)
    upsert_all(store, decisions, events, transitions)
    store.set_snapshot_etag(snapshot_etag)

    # Catalogs
    fields = build_field_catalog(decisions, events, transitions)
    rels = build_relation_catalog()
    store.set_field_catalog(fields); store.set_relation_catalog(rels)

    log_stage(logger, "ingest", "seed_persist_ok",
              snapshot_etag=snapshot_etag,
              fields=len(fields), relations=len(rels))

    print(json.dumps({"ok": True, "files": len(files), "snapshot_etag": snapshot_etag}))
    return 0

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "seed":
        print("usage: python -m ingest.cli seed <dir>", file=sys.stderr)
        sys.exit(1)
    sys.exit(seed(sys.argv[2]))

if __name__ == "__main__":
    main()
