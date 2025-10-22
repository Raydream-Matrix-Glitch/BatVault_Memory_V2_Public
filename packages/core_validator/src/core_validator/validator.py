from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Iterable
from jsonschema import Draft202012Validator, RefResolver
import core_models  # used to locate the canonical schemas directory
from core_logging import get_logger, log_stage, current_request_id

logger = get_logger("core_validator")
_SCHEMA_DIR_LOGGED = False

def _verbose() -> bool:
    # Reduce noise by default; set VALIDATOR_VERBOSE=1 to enable per-item logs.
    return os.getenv("VALIDATOR_VERBOSE", "0") == "1"

# ── Drift-proof helpers (derive rules from shared schemas) ─────────────────────
_SCHEMA_CACHE: dict[str, dict] = {}

def _load_schema(name: str) -> dict:
    """Load a JSON Schema by filename from the canonical core_models/schemas dir."""
    d = _schemas_dir() / name
    if name not in _SCHEMA_CACHE:
        with open(d, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE[name] = json.load(f)
        if not globals().get("_SCHEMA_DIR_LOGGED", False):
            log_stage(
                logger, "schemas", "loaded",
                schema_dir=str(_schemas_dir()), schema=name,
                request_id=(current_request_id() or "startup")
            )
    return _SCHEMA_CACHE[name]

def _load_schema_store() -> dict:
    """Build a $ref store for the validator (needed for nested $ref)."""
    store = {}
    for fname in ("decision.json","event.json","edge.json","edge.wire.json","edge.oriented.json",
                  "memory.meta.json","memory.graph_view.json","bundles.exec_summary.json",
                  "bundles.view.json","bundles.trace.json"):
        try:
            sch = _load_schema(fname)
            store[sch.get("$id", fname)] = sch
        except FileNotFoundError:
            continue
    return store

def _ts_regex_from_schema() -> re.Pattern:
    """Compile the timestamp regex from edge.wire.json to avoid drift."""
    pattern = (((_load_schema("edge.wire.json") or {}).get("properties") or {}).get("timestamp") or {}).get("pattern")
    if not pattern:
        # Fallback to the Baseline v3 shape (UTC-Z, seconds precision).
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    return re.compile(pattern)

def _orientation_keys_from_schema() -> tuple[str, ...]:
    """Return keys that indicate orientation in oriented edges (usually {'orientation'})."""
    props = (((_load_schema("edge.oriented.json") or {}).get("properties") or {}))
    keys: list[str] = []
    if "orientation" in props:
        keys.append("orientation")
    return tuple(keys) or ("orientation",)

# Resolve patterns once (update on process restart if schema changes)
_TS_RE = _ts_regex_from_schema()
_ORIENTATION_KEYS = _orientation_keys_from_schema()

# ---------- schema loading (Baseline v3) ----------
#
def _schemas_dir() -> Path:
    """
    Resolve the authoritative JSON Schemas directory.
    Precedence:
      1) Env var BATVAULT_SCHEMAS_DIR (absolute or relative to CWD)
      2) Default: packages/core_models/src/core_models/schemas
    Note: We never fall back to services/ingest paths at runtime.
    """
    global _SCHEMA_DIR_LOGGED
    env = os.getenv("BATVAULT_SCHEMAS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            if not _SCHEMA_DIR_LOGGED:
                logger.info("core_validator.schemas_dir_env", extra={"dir": str(p)})
                _SCHEMA_DIR_LOGGED = True
            return p
        if not _SCHEMA_DIR_LOGGED:
            logger.warning("core_validator.schemas_dir_missing", extra={"dir": env})
            _SCHEMA_DIR_LOGGED = True
    default_dir = (Path(core_models.__file__).parent / "schemas").resolve()
    if not _SCHEMA_DIR_LOGGED:
        logger.info("core_validator.resolved_schemas_dir", extra={"dir": str(default_dir)})
        _SCHEMA_DIR_LOGGED = True
    return default_dir

def _load_schema(name: str) -> Dict[str, Any]:
    p = _schemas_dir() / name
    if not p.exists():
        raise FileNotFoundError(f"schema not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_schema_store() -> Dict[str, Any]:
    """Load all JSON Schemas from the resolved schemas directory into a $id→schema store.
    This prevents any network fetching when resolving $ref, even if schemas use
    absolute HTTPS $id values (e.g., https://batvault.dev/schemas/*.json)."""
    store: Dict[str, Any] = {}
    schema_dir = _schemas_dir()
    for p in schema_dir.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                s = json.load(f)
            sid = (s or {}).get("$id")
            if isinstance(sid, str) and sid:
                store[sid] = s
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("core_validator.schema_store_load_error", extra={"path": str(p), "error": str(exc)})
    # Strategic log: helps auditing what we can resolve locally.
    logger.info("core_validator.schema_resolver_store", extra={"ids": sorted(list(store.keys()))})
    return store

# ---------- optional node-level validators (lazy) ----------
_NODE_VALIDATOR_CACHE: Dict[str, Draft202012Validator] = {}

def _get_node_validator(name: str) -> Draft202012Validator:
    """
    Lazily load node-level validators when requested (e.g., ingest write-time).
    Does not run at import time to avoid hard failures if node schemas are absent.
    """
    v = _NODE_VALIDATOR_CACHE.get(name)
    if v is not None:
        return v
    schema = _load_schema(name)
    v = Draft202012Validator(schema)
    _NODE_VALIDATOR_CACHE[name] = v
    return v

# ---------- invariants for views (fail-closed) ----------
_ORIENTATION_KEYS = {"orientation"}
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_LEGACY_KEYS = {"neighbors", "rel", "direction", "transitions", "mirrors", "options"}

def _legacy_field_errors(payload: Any, path: Tuple[str, ...] = ()) -> List[str]:
    errs: List[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in _LEGACY_KEYS:
                where = ".".join(path + (k,)) or "<root>"
                errs.append(f"legacy field {k!r} present at {where}")
            errs.extend(_legacy_field_errors(v, path + (str(k),)))
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            errs.extend(_legacy_field_errors(v, path + (str(i),)))
    return errs

def _wire_orientation_errors(edges: Iterable[dict]) -> List[str]:
    """
    Memory wire view MUST NOT include orientation on any edge.
    Orientation exists only in bundles (Gateway, causal edges only).
    """
    errs: List[str] = []
    try:
        for i, e in enumerate(edges or []):
            # forbid oriented-only keys from appearing on the wire view
            if any(k in e for k in _ORIENTATION_KEYS):
                errs.append(f"edge[{i}] orientation not allowed on memory wire view")
    except (TypeError, AttributeError, KeyError) as exc:
        errs.append(f"wire orientation check error: {exc!s}")
    return errs

def _timestamp_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    errs: List[str] = []
    for i, e in enumerate(edges or []):
        ts = e.get("timestamp")
        if not isinstance(ts, str) or not _TS_RE.match(ts):
            errs.append(f"edge[{i}] invalid timestamp (schema:{_TS_RE.pattern}): {ts!r}")
    if errs and _verbose():
        log_stage(
            logger, "validate", "timestamp_rejected",
            error_count=len(errs), sample_error=errs[0],
            request_id=(current_request_id() or "unknown")
        )
    return errs

def _duplicate_edge_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    errs: List[str] = []
    seen = set()
    for e in edges or []:
        key = (e.get("type"), e.get("from"), e.get("to"), e.get("timestamp"))
        if key in seen:
            errs.append(f"duplicate edge detected: {key!r}")
        else:
            seen.add(key)
    return errs


def _bundle_orientation_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    """Enforce orientation invariants on **bundle** edges (Baseline v3).
    - REQUIRED orientation on {LED_TO, CAUSAL}
    - FORBIDDEN orientation on ALIAS_OF
    - Value ∈ {"preceding","succeeding"}
    """
    errs: List[str] = []
    for i, e in enumerate(edges or []):
        try:
            et = str((e or {}).get("type") or "").upper()
            orient = (e or {}).get("orientation")
            if et == "ALIAS_OF":
                if orient is not None:
                    errs.append(f"edge[{i}] ALIAS_OF must not have orientation")
            elif et in ("LED_TO", "CAUSAL"):
                if orient is None:
                    errs.append(f"edge[{i}] missing orientation for {et}")
                elif str(orient) not in ("preceding","succeeding"):
                    errs.append(f"edge[{i}] invalid orientation: {orient!r}")
        except (TypeError, AttributeError, KeyError, ValueError) as exc:
            errs.append(f"edge[{i}] orientation check error: {exc!s}")
    return errs

def validate_node(obj: dict) -> Tuple[bool, List[str]]:
    t = (obj or {}).get("type")
    if t == "EVENT":
        try:
            validator = _get_node_validator("event.json")
            errors = [e.message for e in validator.iter_errors(obj)]
        except FileNotFoundError as e:
            errors = [str(e)]
    elif t == "DECISION":
        try:
            validator = _get_node_validator("decision.json")
            errors = [e.message for e in validator.iter_errors(obj)]
        except FileNotFoundError as e:
            errors = [str(e)]
    else:
        errors = [f"unknown node type: {t!r}"]
    ok = len(errors) == 0
    if _verbose():
        log_stage(
            logger, "validate", "node_ok" if ok else "node_invalid",
            node_id=obj.get("id"), node_type=t,
            error_count=len(errors),
            sample_error=(errors[0] if errors else None),
        )
    return ok, errors

def validate_edge(obj: dict) -> Tuple[bool, List[str]]:
    try:
        validator = _get_node_validator("edge.json")
        errors = [e.message for e in validator.iter_errors(obj)]
    except FileNotFoundError as e:
        errors = [str(e)]
    ok = len(errors) == 0
    if _verbose():
        log_stage(
            logger, "validate", "edge_ok" if ok else "edge_invalid",
            edge_id=obj.get("id"),
            error_count=len(errors),
            sample_error=(errors[0] if errors else None),
        )
    return ok, errors

# ---------- new: view validators (Memory→Gateway) ----------
def validate_graph_view(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate Memory→Gateway graph view (edges-only; Baseline v3):
      - envelope: {anchor, graph:{edges}, meta} against core_models/schemas
      - reject legacy fields anywhere (neighbors/rel/direction/transitions/mirrors/options)
      - enforce seconds-precision UTC-Z timestamps
      - detect duplicate edges by (type, from, to, timestamp)
    """
    errors: List[str] = []
    # 1) fail-closed: legacy fields
    errors.extend(_legacy_field_errors(payload))
    # 2) schema validation with $ref to memory.meta.json
    try:
        graph_schema = _load_schema("memory.graph_view.json")
        store = _load_schema_store()
        validator = Draft202012Validator(
            graph_schema, resolver=RefResolver.from_schema(graph_schema, store=store)
        )
    except FileNotFoundError as e:
        errors.append(str(e))
    # 2b) Hard guard: Memory MUST NOT emit 'orientation' on edges.
    #     Orientation is owned by Gateway bundles only (see edge.oriented.json).
    try:
        mem_edges = ((payload or {}).get("graph") or {}).get("edges") or []
        for i, e in enumerate(mem_edges):
            if isinstance(e, dict) and "orientation" in e:
                errors.append(
                    f"edge[{i}] must not contain 'orientation' in memory.graph_view"
                )
    except Exception as exc:
        errors.append(f"orientation guard error: {exc!s}")
    # Additional invariants for bundle edges: seconds-only timestamps + orientation rules
    try:
        resp = (payload or {}).get("response.json") or {}
        if isinstance(resp, dict) and "response" in resp and "schema_version" in resp:
            resp = resp.get("response") or {}
        edges = ((resp.get("graph") or {}).get("edges") or []) if isinstance(resp, dict) else []
        errors.extend(_timestamp_errors(edges))
        errors.extend(_bundle_orientation_errors(edges))
    except Exception as exc:
        errors.append(f"bundle edge invariant check error: {exc!s}")
    # 3) timestamp / duplicate checks on edges
    edges = ((payload or {}).get("graph") or {}).get("edges") or []
    errors.extend(_timestamp_errors(edges))
    errors.extend(_duplicate_edge_errors(edges))
    # On Memory graph view (not bundle), orientation must be absent.
    # If the payload looks like a signed bundle (has top-level 'response'), orientation checks
    # are performed in _bundle_orientation_errors instead.
    if not (isinstance(payload, dict) and "response" in payload and "schema_version" in payload):
        _wire_edges = ((payload or {}).get("graph") or {}).get("edges") or []
        w_errs = _wire_orientation_errors(_wire_edges)
        if w_errs and _verbose():
            log_stage(logger, "validate", "memory_wire_orientation_rejected",
                      error_count=len(w_errs), sample_error=w_errs[0])
        errors.extend(w_errs)
    ok = len(errors) == 0
    log_stage(
        logger, "validate", "graph_view_ok" if ok else "graph_view_invalid",
        error_count=len(errors),
        edges_count=len(edges),
        sample_error=(errors[0] if errors else None),
    )
    return ok, errors

def validate_bundle_view(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate Gateway bundle view (response.json) against core_models/schemas/bundles.view.json.
    Also rejects legacy fields anywhere in the structure.
    """
    errors: List[str] = []
    errors.extend(_legacy_field_errors(payload))
    try:
        view_schema  = _load_schema("bundles.view.json")
        store        = _load_schema_store()
        resolver     = RefResolver.from_schema(view_schema, store=store)
        validator    = Draft202012Validator(view_schema, resolver=resolver)
        def _fmt(e):
            path = ".".join(str(p) for p in e.path) or "<root>"
            rule = "/".join(str(p) for p in e.schema_path)
            return f"{e.message} @ {path} (rule:{rule})"
        errors.extend([_fmt(e) for e in validator.iter_errors(payload)])
    except FileNotFoundError as e:
        errors.append(str(e))
    ok = len(errors) == 0
    log_stage(
        logger, "validate", "bundle_view_ok" if ok else "bundle_view_invalid",
        error_count=len(errors),
        sample_error=(errors[0] if errors else None),
    )
    return ok, errors

__all__ = ["validate_node", "validate_edge", "validate_graph_view", "validate_bundle_view"]