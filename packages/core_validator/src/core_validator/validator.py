from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Iterable
from jsonschema import Draft202012Validator, RefResolver
import core_models  # used to locate the canonical schemas directory
from core_logging import get_logger, log_stage, current_request_id
from core_utils import jsonx
from core_models.ontology import canonical_edge_type, CAUSAL_EDGE_TYPES, ALIAS_EDGE_TYPES

logger = get_logger("core_validator")
_SCHEMA_DIR_LOGGED = False

def _verbose() -> bool:
    # Reduce noise by default; set VALIDATOR_VERBOSE=1 to enable per-item logs.
    return os.getenv("VALIDATOR_VERBOSE", "0") == "1"

# ── Baseline v3: schema loader (source of truth) ───────────────────────────────

_NODE_VALIDATOR_CACHE: Dict[str, Draft202012Validator] = {}
_SCHEMA_STORE: Dict[str, Dict[str, Any]] | None = None

def _schemas_dir() -> Path:
    """
    Resolve the schemas directory used by the validator.
    Priority:
      1) BATVAULT_SCHEMAS_DIR (explicit override for tests / dev)
      2) core_models/schemas (canonical, versioned with the repo)
    """
    global _SCHEMA_DIR_LOGGED
    env_dir = os.getenv("BATVAULT_SCHEMAS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser().resolve()
        if p.exists():
            if not _SCHEMA_DIR_LOGGED:
                logger.info("core_validator.resolved_schemas_dir", extra={"dir": str(p)})
                _SCHEMA_DIR_LOGGED = True
            return p
        else:
            logger.warning("core_validator.schemas_dir_missing_env", extra={"dir": str(p)})
            # fall through to default
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
    for p in sorted(schema_dir.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                s = json.load(f)
            sid = (s or {}).get("$id")
            if isinstance(sid, str) and sid:
                store[sid] = s
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("core_validator.schema_store_load_error", extra={"path": str(p), "error": str(exc)})
    logger.info("core_validator.schema_resolver_store", extra={"ids": sorted(list(store.keys()))})
    return store

# ── Bundle view helpers (shared) ----------------------------------------------
def view_artifacts_allowed() -> frozenset[str]:
    """
    Return the set of allowable artifact names in bundles.view.json.
    """
    try:
        schema = _load_schema("bundles.view.json")
    except FileNotFoundError:
        return frozenset()
    props = (schema.get("properties") or {})
    return frozenset(props.keys()) if props else frozenset()

def build_bundle_view(
    resp: Dict[str, Any],
    artifacts: dict[str, bytes] | None,
    *,
    report_version: str = "1.1",
) -> Dict[str, Any]:
    """
    Construct a 'bundle view' dictionary from a response object and optional artifacts.
    Only properties allowed by bundles.view.json are included. response.json is
    sanitized and preferred from artifacts when available.
    """
    allowed = view_artifacts_allowed()
    bundle: Dict[str, Any] = {}
    # Load serialized artifacts (JSON), excluding response.json for now.
    if artifacts:
        for name, raw in artifacts.items():
            if name not in allowed or name == "response.json":
                continue
            try:
                bundle[name] = jsonx.loads(raw)
            except (ValueError, TypeError):
                bundle[name] = {}
    # Prefer serialized response.json if present; else sanitize in-memory resp.
    if artifacts and "response.json" in artifacts:
        try:
            bundle["response.json"] = jsonx.loads(artifacts["response.json"])
        except (ValueError, TypeError):
            bundle["response.json"] = (resp if isinstance(resp, dict) else {})
    else:
        bundle["response.json"] = (resp if isinstance(resp, dict) else {})
    # Ensure a minimal validator_report for downstream consumers/tests.
    if "validator_report.json" in allowed and "validator_report.json" not in bundle:
        bundle["validator_report.json"] = {"version": report_version, "pass": True, "errors": [], "checks": []}
    return bundle

# ---------- derived patterns (computed after schema loaders exist) ----------

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

def _get_node_validator(name: str) -> Draft202012Validator:
    """
    Lazily load node-level validators when requested (e.g., ingest write-time).
    Does not run at import time to avoid hard failures if node schemas are absent.
    """
    v = _NODE_VALIDATOR_CACHE.get(name)
    if v is not None:
        return v
    schema = _load_schema(name)
    resolver = RefResolver.from_schema(schema, store=_SCHEMA_STORE or _load_schema_store())
    validator = Draft202012Validator(schema, resolver=resolver, format_checker=None)
    _NODE_VALIDATOR_CACHE[name] = validator
    return validator

def _get_view_validator(name: str) -> Draft202012Validator:
    """
    Validators for memory.graph_view / bundle.view shapes.
    """
    schema = _load_schema(name)
    resolver = RefResolver.from_schema(schema, store=_SCHEMA_STORE or _load_schema_store())
    return Draft202012Validator(schema, resolver=resolver, format_checker=None)

# ── Edge / graph invariants ----------------------------------------------------

def _timestamp_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    errs: List[str] = []
    for i, e in enumerate(edges or []):
        ts = e.get("timestamp")
        if not isinstance(ts, str) or not _TS_RE.match(ts):
            errs.append(f"edge[{i}].timestamp must be RFC3339 Z seconds precision (e.g., 2024-06-01T12:34:56Z)")
    return errs

def _duplicate_edge_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    """Detect exact duplicate edges (same from,to,type,timestamp)."""
    seen: set[Tuple[Any, Any, Any, Any]] = set()
    dupes: List[str] = []
    for i, e in enumerate(edges or []):
        key = (e.get("from"), e.get("to"), e.get("type"), e.get("timestamp"))
        if key in seen:
            dupes.append(f"duplicate edge at index {i}: {key}")
        else:
            seen.add(key)
    return dupes

def _bundle_orientation_errors(edges: Iterable[Dict[str, Any]]) -> List[str]:
    errs: List[str] = []
    for i, e in enumerate(edges or []):
        # Each edge must be a mapping; non-dicts are invalid.
        if e is None or not isinstance(e, dict):
            errs.append(f"edge[{i}] must be an object")
            continue
        try:
            et = canonical_edge_type(e.get("type"))
        except ValueError:
            continue  # Unknown types are ignored for orientation checks
        has_orientation = any(k in e for k in _ORIENTATION_KEYS)
        if et in set(CAUSAL_EDGE_TYPES):
            # Causal edges MUST carry an orientation key.
            if not has_orientation:
                errs.append(f"edge[{i}] missing orientation key(s): {_ORIENTATION_KEYS}")
        elif et in set(ALIAS_EDGE_TYPES):
            # Alias edges MUST NOT have orientation.
            if has_orientation:
                errs.append(
                    f"edge[{i}] must not contain orientation key(s): {_ORIENTATION_KEYS} on ALIAS_OF edge"
                )
        else:
            # Unknown types are ignored for orientation checks.
            pass
    return errs

# ── Public validators ---------------------------------------------------------

def validate_node(kind: str, payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate a single node (decision/event/etc.) against its schema.
    """
    errors: List[str] = []
    try:
        validator = _get_node_validator(f"{kind}.json")
        for e in validator.iter_errors(payload):
            path = ".".join(str(p) for p in e.path) or "<root>"
            rule = "/".join(str(p) for p in e.schema_path)
            errors.append(f"{e.message} @ {path} (rule:{rule})")
    except FileNotFoundError as e:
        errors.append(str(e))
    ok = len(errors) == 0
    if _verbose() or not ok:
        log_stage(
            logger, "validate", "node_ok" if ok else "node_invalid",
            node=kind, error_count=len(errors),
            sample_error=(errors[0] if errors else None),
            request_id=(current_request_id() or "import"),
        )
    return ok, errors

def validate_edge(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate one edge (wire shape) against schema + invariants.
    """
    errors: List[str] = []
    try:
        validator = _get_view_validator("edge.wire.json")
        for e in validator.iter_errors(payload):
            path = ".".join(str(p) for p in e.path) or "<root>"
            rule = "/".join(str(p) for p in e.schema_path)
            errors.append(f"{e.message} @ {path} (rule:{rule})")
    except FileNotFoundError as e:
        errors.append(str(e))
    # invariants
    errors.extend(_timestamp_errors([payload]))
    ok = len(errors) == 0
    if _verbose() or not ok:
        log_stage(
            logger, "validate", "edge_ok" if ok else "edge_invalid",
            error_count=len(errors),
            sample_error=(errors[0] if errors else None),
        )
    return ok, errors

def validate_graph_view(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate the memory.graph_view (edges only) returned by Memory API.
    Ensures schema correctness and enforces fail-closed invariants.
    """
    errors: List[str] = []
    try:
        validator = _get_view_validator("memory.graph_view.json")
        for e in validator.iter_errors(payload):
            path = ".".join(str(p) for p in e.path) or "<root>"
            rule = "/".join(str(p) for p in e.schema_path)
            errors.append(f"{e.message} @ {path} (rule:{rule})")
    except FileNotFoundError as e:
        errors.append(str(e))
    # 1) timestamp / duplicate checks on edges
    edges = ((payload or {}).get("graph") or {}).get("edges") or []
    errors.extend(_timestamp_errors(edges))
    errors.extend(_duplicate_edge_errors(edges))
    # On Memory graph view (not bundle), orientation must be absent.
    # If the payload looks like oriented edges, raise an error.
    try:
        for i, e in enumerate(edges):
            if isinstance(e, dict) and any(k in e for k in _ORIENTATION_KEYS):
                errors.append(f"edge[{i}] must not contain orientation keys: {_ORIENTATION_KEYS} in memory.graph_view")
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        errors.append(f"orientation guard error: {exc!s}")
    ok = len(errors) == 0
    log_stage(
        logger, "validate", "graph_view_ok" if ok else "graph_view_invalid",
        error_count=len(errors),
        sample_error=(errors[0] if errors else None),
    )
    return ok, errors

def validate_bundle_view(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate the bundle.view (Gateway response envelope) against schema + invariants.
    """
    errors: List[str] = []
    try:
        validator = _get_view_validator("bundles.view.json")
        for e in validator.iter_errors(payload):
            path = ".".join(str(p) for p in e.path) or "<root>"
            rule = "/".join(str(p) for p in e.schema_path)
            errors.append(f"{e.message} @ {path} (rule:{rule})")
    except FileNotFoundError as e:
        errors.append(str(e))
    # Additional invariants for bundle edges: seconds-only timestamps + orientation rules
    try:
        resp = (payload or {}).get("response.json") or {}
        if isinstance(resp, dict) and "response" in resp and "schema_version" in resp:
            resp = resp.get("response") or {}
        edges = ((resp.get("graph") or {}).get("edges") or []) if isinstance(resp, dict) else []
        errors.extend(_timestamp_errors(edges))
        errors.extend(_bundle_orientation_errors(edges))
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        errors.append(f"bundle edge invariant check error: {exc!s}")
    # 3) timestamp / duplicate checks on edges
    edges = ((payload or {}).get("graph") or {}).get("edges") or []
    errors.extend(_timestamp_errors(edges))
    errors.extend(_duplicate_edge_errors(edges))
    ok = len(errors) == 0
    log_stage(
        logger, "validate", "bundle_view_ok" if ok else "bundle_view_invalid",
        error_count=len(errors),
        sample_error=(errors[0] if errors else None),
    )
    return ok, errors

__all__ = [
    "validate_node",
    "validate_edge",
    "validate_graph_view",
    "validate_bundle_view",
    "build_bundle_view",
    "view_artifacts_allowed",
]
