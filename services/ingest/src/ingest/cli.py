import sys, json, os, glob, re, time, argparse, hashlib
from importlib import resources 
from jsonschema import Draft202012Validator
from core_logging import get_logger, log_stage
from core_utils import compute_snapshot_etag_for_files, slugify_id
from core_storage import ArangoStore
from core_config import get_settings
from .pipeline.normalize import normalize_decision, normalize_event, normalize_transition, derive_backlinks
from .pipeline.snippet_enricher import enrich_all as enrich_snippets
from .pipeline.graph_upsert import upsert_all
from .catalog.field_catalog import build_field_catalog, build_relation_catalog


logger = get_logger("ingest-cli")
settings = get_settings()

# ------------------------------------------------------------------
#  Public regex constants needed by the contract-tests
# ------------------------------------------------------------------

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$")

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
    """
    Works both from the repo **and** once the package is installed in site-packages.
    """
    pkg = "ingest.schemas.json_v2"
    path = resources.files(pkg).joinpath(f"{name}.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))

SC_DECISION = load_schema("decision")
SC_EVENT = load_schema("event")
SC_TRANSITION = load_schema("transition")

def validate_doc(doc: dict, kind: str, path: str) -> list[str]:
    sch = {"decision": SC_DECISION, "event": SC_EVENT, "transition": SC_TRANSITION}[kind]
    v = Draft202012Validator(sch)
    errs = [f"{path}: {e.message}" for e in sorted(v.iter_errors(doc), key=lambda e: e.path)]
    return errs

def seed(path: str) -> int:
    # Recursively gather all JSON fixtures from nested subfolders
    files = sorted(
        glob.glob(os.path.join(path, "**", "*.json"), recursive=True)
    )
    if not files:
        # strategic structured logging for fast triage
        log_stage(
            logger, "ingest", "fixture_scan_empty",
            search_path=os.path.abspath(path),
            deterministic_id=slugify_id(os.path.abspath(path)),
        )
        print(f"No JSON files found under {path}", file=sys.stderr)
        return 1

    raw_decisions, raw_events, raw_transitions = {}, {}, {}
    errors = []
    alias_stats = {"hits": 0, "docs": 0}
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as e:
            if getattr(e, "lineno", None):                       # surface file/line diagnostics
                errors.append(f"{p}:{e.lineno}:{e.colno}: json error {e.msg}")
            else:
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

    # Enrich (deterministic) — precompute node-level `snippet`
    enrich_snippets(decisions, events, transitions)

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
    upsert_all(store, decisions, events, transitions, snapshot_etag)
    store.set_snapshot_etag(snapshot_etag)

    # ---------- 🎯  content-addressable batch snapshot (spec §D) ----------
    from core_storage.minio_utils import ensure_bucket
    import minio, io, gzip, orjson, datetime as _dt
    minio_client = minio.Minio(settings.minio_endpoint,
                               access_key=settings.minio_access_key,
                               secret_key=settings.minio_secret_key,
                               secure=False)
    ensure_bucket(minio_client, "batvault-snapshots", 30)
    blob = gzip.compress(orjson.dumps({"decisions": decisions,
                                       "events": events,
                                       "transitions": transitions}))
    obj_name = f"{snapshot_etag}.json.gz"
    minio_client.put_object("batvault-snapshots", obj_name,
                            io.BytesIO(blob), length=len(blob),
                            content_type="application/gzip")
    log_stage(logger, "artifacts", "snapshot_uploaded",
              bucket="batvault-snapshots", object=obj_name, size=len(blob))

    # --------------------  sweep out stale docs  -------------------
    removed_nodes, removed_edges = store.prune_stale(snapshot_etag)
    log_stage(
        logger,
        "ingest",
        "prune_stale",
        snapshot_etag=snapshot_etag,
        removed_nodes=removed_nodes,
        removed_edges=removed_edges,
    )

    # Catalogs
    fields = build_field_catalog(decisions, events, transitions)
    rels = build_relation_catalog()
    store.set_field_catalog(fields); store.set_relation_catalog(rels)

    log_stage(logger, "ingest", "seed_persist_ok",
              snapshot_etag=snapshot_etag,
              fields=len(fields), relations=len(rels))

    # --- Final strategic summary for dashboards/audit -------------------
    dt_total_ms = int((time.perf_counter() - t0) * 1000) if 't0' in locals() else None
    log_stage(
        logger, "ingest", "batch_completed",
        ingest_batch_id=_stable_id(snapshot_etag),
        snapshot_etag=snapshot_etag,
        files=len(files),
        decisions=len(decisions), events=len(events), transitions=len(transitions),
        alias_hits=alias_stats.get("hits", 0),
        ri_errors=0,
        removed_nodes=removed_nodes, removed_edges=removed_edges,
        latency_ms=dt_total_ms,
    )
    print(json.dumps({"ok": True, "files": len(files), "snapshot_etag": snapshot_etag}))
    return 0

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ingest-cli",
        description="Seed Batvault graph from fixture directory",
    )
    parser.add_argument("command", choices=["seed"], help="Currently only 'seed' is supported")
    parser.add_argument("dir", help="Directory that contains decision/event/transition JSON files")
    parser.add_argument(
        "--arango-url",
        help="Override the ARANGO_URL env var for this run "
             "(e.g. --arango-url http://localhost:8529)",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    #  Strategic logging – resolved Arango URL
    # ------------------------------------------------------------------
    if args.arango_url:
        settings.arango_url = args.arango_url        # runtime override
        logger.info(
            "override_arango_url",
            extra={
                "stage": "ingest",
                "arango_url": settings.arango_url,
                "deterministic_id": _stable_id(settings.arango_url),
            },
        )
    else:
        logger.info(
            "resolved_arango_url",
            extra={
                "stage": "ingest",
                "arango_url": settings.arango_url,
                "deterministic_id": _stable_id(settings.arango_url),
            },
        )
    
    if args.command == "seed":
        sys.exit(seed(args.dir))

# ------------------------------------------------------------------
#  helpers
# ------------------------------------------------------------------

def _stable_id(value: str) -> str:
    """Return an 8‑char deterministic slug for log correlation."""
    return hashlib.sha1(value.encode()).hexdigest()[:8]

if __name__ == "__main__":
    main()
