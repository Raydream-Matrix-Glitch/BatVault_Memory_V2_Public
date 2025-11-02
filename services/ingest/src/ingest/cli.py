import sys, os, argparse, json, glob
from pathlib import Path
from json import JSONDecodeError
from typing import Dict, Any
from core_logging import get_logger, log_stage, trace_span, set_snapshot_etag
from core_utils.snapshot import compute_snapshot_etag_for_files
from core_config import get_settings
from core_storage import ArangoStore
from ingest.pipeline.graph_upsert import upsert_pipeline, compute_expected_edges
if os.getenv("BATVAULT_INGEST_PROCESS") != "1":
    print("ERROR: BATVAULT_INGEST_PROCESS=1 required for ingest environment", file=sys.stderr)
    sys.exit(2)
from ingest.pipeline.normalize import normalize_once

logger = get_logger("ingest.cli")

def _load_json_files(dir_path: Path) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    for path in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (JSONDecodeError, OSError) as e:
            raise SystemExit(f"Failed to parse {path}: {e}")
        def _is_node(obj: dict) -> bool:
            t = (obj.get("type") or "").upper()
            return t in ("DECISION","EVENT")
        def _is_edge(obj: dict) -> bool:
            t = (obj.get("type") or "").upper()
            return t in ("LED_TO","CAUSAL","ALIAS_OF")

        if isinstance(data, dict):
            if _is_node(data):
                nodes.append(data)
            elif _is_edge(data):
                edges.append(data)
        elif isinstance(data, list):
            for obj in data:
                if _is_node(obj):
                    nodes.append(obj)
                elif _is_edge(obj):
                    edges.append(obj)
    return nodes, edges

def run_dir(dir_path: str) -> int:
    p = Path(dir_path)
    if not p.exists():
        raise SystemExit(f"Directory not found: {dir_path}")
    nodes, edges = _load_json_files(p)
    snapshot_etag = compute_snapshot_etag_for_files([str(x) for x in p.glob("*.json")])
    set_snapshot_etag(snapshot_etag)
    log_stage(logger, "ingest", "cli_start", snapshot_etag=snapshot_etag,
              node_count=len(nodes), edge_count=len(edges))
    # Normalize once
    nodes, edges = normalize_once(nodes, edges)

    # Storage + upsert
    # Silence Arango bootstrap logs by default (opt-in via ARANGO_BOOTSTRAP_VERBOSE=1)
    os.environ.setdefault("ARANGO_BOOTSTRAP_VERBOSE", "0")
    store = ArangoStore(lazy=True)
    # Optional deterministic pruning to avoid 409s and prevent stale data.
    if os.getenv("ARANGO_PRUNE_BEFORE_UPSERT", "1") == "1":
        planned_edges = compute_expected_edges(nodes, edges, snapshot_etag=snapshot_etag)
        anchors = [f"{n['domain']}#{n['id']}" for n in nodes]
        edge_ids = [e["id"] for e in planned_edges]
        with trace_span("ingest.cli.prune", stage="ingest"):
            n_nodes, n_edges, cleaned = store.prune_to_current_snapshot(
                anchors, edge_ids, request_id=snapshot_etag
            )
        log_stage(
            logger, "ingest", "pruned",
            snapshot_etag=snapshot_etag,
            nodes_removed=int(n_nodes),
            edges_removed=int(n_edges),
            fields_cleaned=int(cleaned),
        )
    with trace_span("ingest.cli.upsert", stage="ingest"):
        summary: Dict[str, Any] = upsert_pipeline(store, nodes, edges, snapshot_etag=snapshot_etag)
    # Persist the new snapshot to meta for read preconditions (Memory reads this)
    try:
        store.set_snapshot_etag(snapshot_etag)
    except (OSError, RuntimeError, ValueError) as e:
        # Fail-fast: snapshot must be persisted for 412 preconditions
        print(f"ERROR: failed to persist snapshot_etag: {e}", file=sys.stderr)
        sys.exit(1)

    # ------ Emit a deterministic final summary event (and one stdout line) ------
    nw = int((summary.get("nodes") or {}).get("written", 0))
    nr = int((summary.get("nodes") or {}).get("rejected", 0))
    ew = int((summary.get("edges") or {}).get("written", 0))
    er = int((summary.get("edges") or {}).get("rejected", 0))
    alias_rej = int(len(summary.get("alias_rejected") or []))
    sens_applied = int(summary.get("sensitivity_applied") or 0)
    log_stage(
        logger, "ingest", "seed_summary",
        snapshot_etag=snapshot_etag,
        nodes_in=len(nodes), nodes_written=nw, nodes_rejected=nr,
        edges_in=len(edges), edges_written=ew, edges_rejected=er,
        alias_rejected=alias_rej, sensitivity_applied=sens_applied,
    )
    # human-friendly line for scripts/CI that don’t parse structured logs
    print(
        f"Seeded snapshot {snapshot_etag}: "
        f"nodes(w={nw},r={nr}/{len(nodes)}) edges(w={ew},r={er}/{len(edges)}); "
        f"alias_rejected={alias_rej} sensitivity_applied={sens_applied}"
    )
    log_stage(logger, "ingest", "completed", snapshot_etag=snapshot_etag)
    return 0

def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    ap = argparse.ArgumentParser("ingest")
    ap.add_argument("dir", help="Directory containing JSON nodes/edges.")
    if argv and argv[0] in ("seed", "load", "upsert"):
        argv = argv[1:]
    args = ap.parse_args(argv)
    try:
        rc = run_dir(args.dir)
    except SystemExit:
        # Preserve non-zero exit
        raise
    except (ValueError, RuntimeError, OSError) as e:
        # Fail fast on first error
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(rc)

if __name__ == "__main__":
    main()