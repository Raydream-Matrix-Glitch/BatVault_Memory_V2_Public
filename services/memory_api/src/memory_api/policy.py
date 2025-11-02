import os, json, fnmatch
from pathlib import Path
from core_utils.fingerprints import canonical_json, sha256_hex, ensure_sha256_prefix, normalize_fingerprint
import orjson
from typing import Dict, Any, List, Optional, Tuple
from core_logging import get_logger, log_stage, log_once
from core_config import get_settings
from core_utils.domain import storage_key_to_anchor, is_valid_anchor
from core_http.headers import REQUIRED_POLICY_HEADERS_LOWER
from core_utils.identity import identity_from_headers

logger = get_logger("memory_api")

# ──────────────────────────────────────────────────────────────────────────────
# Roles / policy configuration (single source of truth: policy/roles.json)
# ──────────────────────────────────────────────────────────────────────────────
_ROLES_CACHE: dict | None = None
_ROLES_MTIME: float | None = None

def _roles_config_path() -> Path:
    """
    Resolve roles.json location:
    1) settings.roles_config_path (if set),
    2) ./policy/roles.json alongside this module,
    3) environment POLICY_ROLES_PATH.
    """
    try:
        s = get_settings()
        p = getattr(s, "roles_config_path", None)
        if p:
            return Path(str(p))
    except (RuntimeError, ValueError, TypeError):
        pass
    env = os.getenv("POLICY_ROLES_PATH")
    if env:
        return Path(env)
    return Path(__file__).parent.joinpath("policy", "roles.json")

def _load_roles_config() -> dict:
    """Load roles.json with a simple mtime cache."""
    global _ROLES_CACHE, _ROLES_MTIME
    path = _roles_config_path()
    try:
        mtime = path.stat().st_mtime
    except (FileNotFoundError, OSError):
        # Safe default if roles.json is missing
        if _ROLES_CACHE is None:
            _ROLES_CACHE = {"roles": {}}
            _ROLES_MTIME = None
        return _ROLES_CACHE
    if _ROLES_CACHE is not None and _ROLES_MTIME == mtime:
        return _ROLES_CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {"roles": {}}
        _ROLES_CACHE = data
        _ROLES_MTIME = mtime
        log_stage(logger, "policy", "roles_config_loaded", path=str(path))
        return _ROLES_CACHE
    except (ValueError, OSError) as e:
        log_stage(logger, "policy", "roles_config_failed", path=str(path), error=type(e).__name__)
        if _ROLES_CACHE is None:
            _ROLES_CACHE = {"roles": {}}
        return _ROLES_CACHE

def _role_entry(role: str) -> dict:
    cfg = _load_roles_config()
    return (cfg.get("roles") or {}).get((role or "").strip().lower(), {}) or {}

def _role_field_visibility(role: str) -> dict:
    return (_role_entry(role).get("field_visibility") or {})

def _role_extra_visible(role: str) -> List[str]:
    ev = _role_entry(role).get("extra_visible")
    if isinstance(ev, list):
        return [str(x) for x in ev if str(x).strip()]
    return []

def _role_default_ceiling(role: str) -> str:
    """
    Optional role-based ceiling in roles.json:
      { "roles": { "<role>": { "sensitivity_ceiling": "medium" } } }
    Fallback to environment/default order if not present.
    """
    ent = _role_entry(role)
    raw = ent.get("sensitivity_ceiling")
    if isinstance(raw, str) and raw.strip():
        return _normalize_ceiling(raw)
    # legacy fallback by role if not configured in roles.json
    defaults = {
        "ceo": "high",
        "admin": "high",
        "manager": "medium",
        "engineer": "medium",
        "analyst": "low",
        "anonymous": "low",
    }
    return defaults.get((role or "").strip().lower(), "low")

def _normalize_ceiling(raw: str) -> str:
    """Return a valid ceiling in the configured order, or 'low'."""
    try:
        val = (raw or "").strip().lower()
    except (ValueError, TypeError, AttributeError):
        return "low"
    if not val:
        return "low"
    order = _sensitivity_order()
    return val if val in set(order) else "low"

def _derive_ceiling_from_role(role: str) -> str:
    """Map a role to its default ceiling via roles.json (fallback to legacy defaults)."""
    return _role_default_ceiling(role)

def compute_effective_policy(headers: Dict[str, str]) -> Dict[str, Any]:
    """
    Minimal, header-derived policy context.
    Role profile (field_visibility/extra_visible) sourced from roles.json;
    OPA may override later at the handler level.
    """
    _require_headers(headers)
    h = _headers_lc(headers)
    ident = identity_from_headers(headers)

    user_id        = (ident.get("user_id") or "").strip()
    policy_version = h["x-policy-version"].strip()
    policy_key_hdr = h["x-policy-key"].strip()
    request_id     = h["x-request-id"].strip()
    trace_id       = h["x-trace-id"].strip()

    roles = ident.get("roles") or []
    # Prefer explicit active role header if your identity helper provides it (BC: first role)
    role = (roles[0] if roles else "").strip().lower()
    if not role:
        raise PolicyHeaderError("missing_required_headers:x-user-roles")

    try:
        _ds = str((h.get("x-denied-status") or "").strip())
        denied_status = 404 if _ds == "404" else 403
    except (ValueError, TypeError):
        denied_status = 403

    # Role defaults from roles.json (overridable via headers and OPA)
    role_profile_vis = _role_field_visibility(role)
    role_extra_default = _role_extra_visible(role)

    extra_visible    = _csv(h.get("x-extra-allow")) or role_extra_default
    user_namespaces  = _csv(h.get("x-user-namespaces"))
    effective_scopes = _csv(h.get("x-domain-scopes"))
    effective_edges  = _csv(h.get("x-edge-allow"))
    raw_cap = (h.get("x-sensitivity-ceiling") or "").strip().lower()
    # Always cap requested ceiling by the role default.
    # Example: manager(default=medium) + header=high  ⇒ effective=medium
    role_default = _derive_ceiling_from_role(role)
    if raw_cap:
        requested  = _normalize_ceiling(raw_cap)
        order      = _sensitivity_order()
        # lower index == less sensitive; take the MIN of the two
        idx_role   = order.index(role_default)
        idx_req    = order.index(requested)
        eff_sens   = order[min(idx_role, idx_req)]
        if eff_sens != requested:
            # Explicitly log when a cap is applied (auditable, deterministic)
            log_stage(
                logger, "policy", "sensitivity_cap_applied",
                role=role, requested=requested, role_default=role_default, effective=eff_sens
            )
    else:
        # No header provided → role default
        eff_sens = role_default


    fp_basis = {
        "user_id": user_id,
        "role": role,
        "namespaces": sorted(user_namespaces),
        "scopes": sorted(effective_scopes),
        "edge_allowlist": sorted(effective_edges),
        "sensitivity": eff_sens,
        "policy_version": policy_version,
        "extra_visible": extra_visible
    }
    computed_fp = normalize_fingerprint(policy_fingerprint(fp_basis))
    if policy_key_hdr and policy_key_hdr != computed_fp:
        log_stage(logger, "policy", "policy_fp_mismatch",
                  provided_fp=policy_key_hdr, computed_fp=computed_fp, request_id=request_id)

    return {
        "user_id": user_id,
        "role": role,
        "role_profile": {"field_visibility": role_profile_vis} if role_profile_vis else {},
        "namespaces": user_namespaces,
        "domain_scopes": effective_scopes,
        "edge_allowlist": effective_edges,
        "sensitivity_ceiling": eff_sens,
        "extra_visible": extra_visible,
        "policy_fp": computed_fp,
        "denied_status": denied_status,
        "request_id": request_id,
        "trace_id": trace_id,
        "policy_version": policy_version
    }


def _filter_x_extra(xextra: Any, allow: List[str]) -> Optional[Dict[str, Any]]:
    """Return a filtered shallow copy of the x-extra object based on an allow-list
    of keys or dot-paths. Supports:
      - ["*"] to allow all keys
      - top-level keys (e.g., "foo")
      - dot-paths (e.g., "a.b.c") to copy nested subtrees
    Unknown/missing paths are ignored. Returns None if nothing allowed.
    """
    if not isinstance(xextra, dict):
        return None
    allow = [str(a).strip() for a in (allow or []) if str(a).strip()]
    if not allow:
        return None
    if "*" in allow:
        # Shallow copy only; never mutate input
        return json.loads(json.dumps(xextra))
    out: Dict[str, Any] = {}
    for path in allow:
        parts = path.split(".")
        cur = xextra
        ok = True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if not ok:
            continue
        # Write into out with the same nested structure
        tgt = out
        for i, p in enumerate(parts):
            if i == len(parts) - 1:
                tgt[p] = cur
            else:
                if p not in tgt or not isinstance(tgt[p], dict):
                    tgt[p] = {}
                tgt = tgt[p]
    return out or None

def policy_fingerprint(policy: Dict[str, Any]) -> str:
    """Canonical fingerprint using core canonical JSON (sorted keys, no microseconds)."""
    try:
        return ensure_sha256_prefix(sha256_hex(canonical_json(policy)))
    except orjson.JSONEncodeError:
        # Never raise on fingerprinting — return a stable sentinel
        return "sha256:unknown"

class PolicyHeaderError(Exception):
    """Raised when required policy/identity headers are missing or invalid."""
    pass

# Canonical list of required headers (fail-closed, no defaults).
# NOTE: keys are matched case-insensitively at runtime.
REQUIRED_HEADERS = list(REQUIRED_POLICY_HEADERS_LOWER) + ["x-request-id", "x-trace-id"]

def _headers_lc(headers: Dict[str, str]) -> Dict[str, str]:
    return {str(k).lower(): v for k, v in (headers or {}).items()}

def _require_headers(headers: Dict[str, str]) -> None:
    h = _headers_lc(headers)
    present = [name for name in REQUIRED_HEADERS if name in h]
    empty   = [name for name in present if not (h.get(name) or "").strip()]
    missing = [name for name in REQUIRED_HEADERS if name not in h]
    if empty or missing:
        log_stage(
            logger, "policy", "missing_headers",
            missing=",".join(missing) if missing else "",
            empty=",".join(empty) if empty else "",
            present=",".join(present),
            request_id=(h.get("x-request-id") or ""),
            trace_id=(h.get("x-trace-id") or ""),
        )
        parts = []
        if missing: parts.append("missing=" + ",".join(missing))
        if empty:   parts.append("empty=" + ",".join(empty))
        raise PolicyHeaderError("missing_required_headers:" + ";".join(parts))

def _csv(header_val: Optional[str]) -> List[str]:
    if not header_val:
        return []
    return [t.strip() for t in str(header_val).split(",") if str(t).strip()]

def _sensitivity_order() -> List[str]:
    """
    Least→most restrictive ordering, from typed settings first, env fallback.
    Mirrors ingest so adding levels is JSON/env only.
    """
    try:
        s = get_settings()
        order = list(getattr(s, "sensitivity_order", [])) or []
    except (AttributeError, RuntimeError, ValueError, TypeError):
        order = []
    if not order:
        order = [x.strip() for x in (os.getenv("SENSITIVITY_ORDER", "low,medium,high") or "").split(",") if x.strip()]
    return order or ["low", "medium", "high"]

_SENS_SYNONYMS = {
    "hi": "high",
    "very_high": "high",
    "confidential": "high",
    "med": "medium",
    "mid": "medium",
    "lo": "low",
}

def _normalize_sensitivity_value(level: Optional[str]) -> str:
    """Normalize arbitrary sensitivity tokens; fail-conservative."""
    val = (level or "low")
    try:
        val = str(val).strip().lower()
    except (RuntimeError, ValueError, TypeError):
        val = "low"
    val = _SENS_SYNONYMS.get(val, val)
    order = set(_sensitivity_order())
    return val if val in order else "high"  # unknown → treat as high

def _sens_rank(level: Optional[str]) -> int:
    order = _sensitivity_order()
    norm = _normalize_sensitivity_value(level)
    try:
        return 1 + order.index(norm)
    except (ValueError, AttributeError):
        return len(order)  # most restrictive

def _min_sensitivity(a: str, b: Optional[str]) -> str:
    if not b:
        return a
    return a if _sens_rank(a) <= _sens_rank(b) else b

def _domain_in_scopes(domain: str, scopes: List[str]) -> bool:
    if not scopes:
        return True
    return any(fnmatch.fnmatch(domain or "", s) for s in scopes)

def _edge_allowed(edge_type: Optional[str], allowlist: List[str]) -> bool:
    if not edge_type:
        return False
    if not allowlist:
        return True
    return edge_type in allowlist

def acl_check(node: Dict[str, Any], policy: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Return (allowed, reason) for ACL evaluation on a node."""
    if not isinstance(node, dict):
        return False, "acl:invalid_node"
    role = policy.get("role")
    namespaces = policy.get("namespaces") or []
    ceiling = policy.get("sensitivity_ceiling") or "low"
    scopes = policy.get("domain_scopes") or []
    node_roles = node.get("roles_allowed") or []
    if node_roles and role not in node_roles:
        return False, "acl:role_missing"
    node_ns = node.get("namespaces") or []
    if node_ns and not (set(node_ns) & set(namespaces)):
        return False, "acl:namespace_mismatch"
    # Normalize sensitivity rigorously; unknown values are treated as 'high'
    node_sens = _normalize_sensitivity_value(node.get("sensitivity") or "low")
    if _sens_rank(node_sens) > _sens_rank(ceiling):
        return False, "acl:sensitivity_exceeded"
    node_domain = node.get("domain") or ""
    if scopes and not _domain_in_scopes(node_domain, scopes):
        return False, "acl:domain_out_of_scope"
    return True, None

def field_mask(node: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply role-based field visibility generically:
    - Iterate role_profile.field_visibility[<type>].visible_fields with support for:
        • "*" (all top-level fields, excluding internals and x-extra),
        • glob patterns (e.g., "decision_maker*", "title*"),
        • dot-path prefixes (e.g., "decision_maker.*" → include the whole top-level object).
    - Special-case only `x-extra` using the role's `extra_visible` list (dot-path aware).
    - Respect optional rationale_visible rule for Decisions.
    No hardcoded field lists → new schema fields appear automatically.
    """
    node = node or {}
    # Avoid inferring a type when missing; use the raw type string only.
    node_type_raw = node.get("type") or ""
    # Reserved fields must never leak into the wire anchor/items
    # NOTE: snapshot_etag is required in the wire meta → do NOT reserve/mask it.
    _reserved = {"meta"}
    node_type_lc = node_type_raw.lower()
    node_type_uc = node_type_raw.upper()
    role_profile = policy.get("role_profile") or {}
    # Merge strategy: OPA explain (if handlers injected it) takes precedence over roles.json seed.
    fv = (role_profile.get("field_visibility") or {}).get(node_type_lc or "", {})
    visible = list(fv.get("visible_fields") or [])
    include_all = any(v == "*" for v in visible)
    # Normalize patterns to top-level keys (dot-path → its first segment).
    top_level_pats = []
    for pat in visible:
        if pat == "x-extra":
            continue
        # Keep entire subtree when pattern is "foo.*"
        if "." in pat:
            top_level_pats.append(pat.split(".", 1)[0])
        else:
            top_level_pats.append(pat)

    # Start with id only; include type only if it exists on the node.
    # Prefer a valid wire anchor if present; otherwise map storage keys to wire form.
    _raw_id = node.get("id")
    _key = node.get("_key")
    _wire_id = None
    if isinstance(_raw_id, str) and is_valid_anchor(_raw_id):
        _wire_id = _raw_id
    elif isinstance(_key, str) and _key:
        _wire_id = storage_key_to_anchor(_key)
        if _raw_id and _raw_id != _wire_id:
            try:
                log_stage(logger, "policy", "id_normalized",
                          before=_raw_id, after=_wire_id, request_id=(policy or {}).get("request_id"))
            except (RuntimeError, ValueError, TypeError):
                pass
    elif isinstance(_raw_id, str) and _raw_id:
        # Fall back: accept storage-style id and convert to wire form deterministically.
        _wire_id = storage_key_to_anchor(_raw_id)
        if _raw_id != _wire_id:
            try:
                log_stage(logger, "policy", "id_normalized",
                          before=_raw_id, after=_wire_id, request_id=(policy or {}).get("request_id"))
            except (RuntimeError, ValueError, TypeError):
                pass
    out: Dict[str, Any] = {"id": _wire_id}
    if node_type_uc:
        out["type"] = node_type_uc
    # Wire contract minimums: never allow policy to strip required fields.
    # Anchors/events require 'domain' on the wire in v3 (§baseline).
    if node.get("domain") is not None:
        out["domain"] = node.get("domain")
        try:
            log_stage(logger, "policy", "required_field_forced",
                      field="domain", request_id=(policy or {}).get("request_id"))
        except (RuntimeError, ValueError, TypeError):
            pass
    # x-extra is governed by extra_visible; handle it first and separately.
    if "x-extra" in visible:
        try:
            filtered = _filter_x_extra(node.get("x-extra"), (policy or {}).get("extra_visible") or [])
            if filtered:
                out["x-extra"] = filtered
        except (TypeError, ValueError):
            pass

    # Copy allowed top-level fields according to patterns.
    for k, v in (node or {}).items():
        if k in {"_key", "_id", "_rev"}:
            continue
        if k in {"id", "type"}:
            continue
        # Hard stop on reserved/leaky fields regardless of role policy
        if k in _reserved:
            # Emit once per request to help catch schema regressions without noisy logs
            try:
                log_once(
                    logger, key=f"masked_reserved:{k}",
                    stage="policy", event="mask.reserved_field", field=k
                )
            except (RuntimeError, ValueError, TypeError):
                pass
            continue
        if k == "x-extra":
            continue  # handled above
        # When include_all is set, include all fields except internal keys and x-extra
        if include_all:
            if v is not None:
                out[k] = v
            continue
        # Match against the normalized top-level patterns
        if any(fnmatch.fnmatch(k, pat) for pat in top_level_pats):
            if v is not None:
                out[k] = v
    return out

def field_mask_with_summary(node: Dict[str, Any], policy: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Apply field_mask and compute a mask_summary (counts + reasons).
    Reasons:
      - policy:field_denied (field not included by visible_fields allow-list or rationale_visible=false)
    NOTE: No sensitive values are included in the summary.
    """
    original = dict(node or {})
    masked = field_mask(node, policy) or {}
    removed_items: List[Dict[str,str]] = []
    node_type = (original.get("type") or "").lower()
    role_profile = policy.get("role_profile") or {}
    fv = (role_profile.get("field_visibility") or {}).get(node_type or "", {})
    visible = set(fv.get("visible_fields") or [])
    for k in list(original.keys()):
        if k in {"_key","_id","_rev","id","type","domain","edge","meta","x-extra"}:
            continue
        if k not in masked:
            # A field was removed because it was not visible under the policy.
            reason_code = "policy:field_denied"
            # Generic rule identifier based on visible_fields; avoid special cases for rationale.
            rule_id = f"{node_type}.visible_fields={','.join(sorted(visible)) or '<empty>'}"
            removed_items.append({"field": k, "reason_code": reason_code, "rule_id": rule_id})
    # x-extra summary of denied fields
    try:
        orig_xe = original.get("x-extra")
        if isinstance(orig_xe, dict):
            allow = (policy or {}).get("extra_visible") or []
            allowed_xe = _filter_x_extra(orig_xe, allow) or {}
            # Flatten keys for comparison
            def _flatten(d, prefix=""):
                items = []
                for k, v in (d or {}).items():
                    p = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        items.extend(_flatten(v, p))
                    else:
                        items.append(p)
                return items
            orig_keys = set(_flatten(orig_xe))
            kept_keys = set(_flatten(allowed_xe))
            for k in sorted(orig_keys - kept_keys):
                removed_items.append({"field": f"x-extra.{k}", "reason_code": "policy:field_denied"})
    except (RuntimeError, ValueError, TypeError):
        pass
    summary = {"total_removed": len(removed_items), "items": removed_items}
    return masked, summary


def filter_and_mask_neighbors(
    neighbors: List[Dict[str, Any]],
    store,
    policy: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Apply edge allowlist + ACL + field masking to the storage-provided neighbor list,
    and reshape to the v3 graph view: neighbors = { events: [], edges: [] }.
    - Memory MUST NOT emit `rel` (orientation). Only emit {type, from, to, timestamp, domain?} for edges.
    - Only EVENT documents are returned in neighbors.events; DECISIONs come via edges + allowed_ids.
    """
    events: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    withheld: Dict[str, str] = {}
    used_edge_types: set[str] = set()
    seen_edges: set[tuple] = set()

    for n in neighbors or []:
        nid = (n or {}).get("id")
        edge = (n or {}).get("edge") or {}
        etype = (edge.get("type") or "").upper()
        if not _edge_allowed(etype, policy.get("edge_allowlist") or []):
            if nid:
                withheld[nid] = "edge_type_blocked"
            continue
        try:
            doc = store.get_node(nid) if hasattr(store, "get_node") else None  # type: ignore[attr-defined]
        except (RuntimeError, OSError, ValueError, TypeError):
            doc = None
        if not doc:
            if nid:
                withheld[nid] = "acl:missing_document"
            continue
        allowed, reason = acl_check(doc, policy)
        if not allowed:
            if nid:
                withheld[nid] = reason or "acl:denied"
            continue

        used_edge_types.add(etype)
        masked = field_mask(doc, policy)

        # EVENT neighbors populate neighbors.events
        if str((masked.get("type") or "")).upper() == "EVENT":
            events.append(masked)

        # Use storage-provided endpoints; Memory never computes orientation (Baseline §2.2.1, §15).
        _from = edge.get('from')
        _to   = edge.get('to')
        if etype in {'LED_TO','CAUSAL','ALIAS_OF'} and _from and _to:
            ed = {
                'type': etype,
                'from': _from,
                'to': _to,
                'timestamp': (edge.get('timestamp') or doc.get('timestamp')),
            }
            if etype == 'ALIAS_OF':
                ev_domain = masked.get('domain')
                if ev_domain:
                    ed['domain'] = ev_domain  # When present, ALIAS_OF.domain equals the alias event’s domain (Baseline §2.2).
            key = (ed['type'], ed['from'], ed['to'], ed.get('timestamp'), ed.get('domain'))
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(ed)

    policy_trace = {
        "withheld_ids": list(withheld.keys()),
        "reasons_by_id": withheld,
        "counts": {"hidden_vertices": len(withheld), "hidden_edges": len(withheld)},
        "edge_types_used": sorted(list(used_edge_types)),
    }
    return {"events": events, "edges": edges}, policy_trace
