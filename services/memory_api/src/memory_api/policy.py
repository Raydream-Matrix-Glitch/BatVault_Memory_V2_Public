import os, json, fnmatch, hashlib
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from core_logging import get_logger, log_stage

SENS_MAP = {"low": 1, "medium": 2, "high": 3}

logger = get_logger("memory_api")

class PolicyHeaderError(Exception):
    """Raised when required policy/identity headers are missing or invalid."""
    pass

# Canonical list of required headers (fail-closed, no defaults).
# NOTE: keys are matched case-insensitively at runtime.
REQUIRED_HEADERS = [
    "x-user-id",
    "x-policy-version",
    "x-policy-key",
    "x-request-id",
    "x-trace-id",
    "x-user-roles",
    "x-user-namespaces",
]

def _headers_lc(headers: Dict[str, str]) -> Dict[str, str]:
    return {str(k).lower(): v for k, v in (headers or {}).items()}

def _require_headers(headers: Dict[str, str]) -> None:
    h = _headers_lc(headers)
    missing = [name for name in REQUIRED_HEADERS if not (h.get(name) or "").strip()]
    if missing:
        # Strategic, structured logging for the audit drawer & ops
        log_stage(
            logger, "policy", "missing_headers",
            missing=",".join(missing),
        )
        raise PolicyHeaderError(f"missing_required_headers:{','.join(missing)}")

@lru_cache(maxsize=32)
def _policy_dir() -> str:
    """
    Resolve the policy registry directory.
    Priority:
      1) $POLICY_DIR
      2) repo-root policy/roles (module-relative)
      3) /app/policy/roles
      4) ./policy/roles (dev CWD)
    """
    here = os.path.abspath(os.path.dirname(__file__))
    repo_roles = os.path.abspath(os.path.join(here, "../../../../policy/roles"))
    candidates = [
        os.getenv("POLICY_DIR"),
        repo_roles,
        "/app/policy/roles",
        os.path.abspath(os.path.join(os.getcwd(), "policy", "roles")),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    # Last-resort default (kept for compatibility; will likely 404 on read)
    return "/app/policy/roles"

@lru_cache(maxsize=64)
def load_role_profile(role: str) -> Dict[str, Any]:
    """Load a role profile JSON from the policy registry."""
    role_token = (role or "").strip().lower()
    fname = f"role-{role_token}.json" if not role_token.startswith("role-") else f"{role_token}.json"
    policy_dir = _policy_dir()
    full = os.path.join(policy_dir, fname)
    try:
        with open(full, "r") as f:
            return json.load(f)
    except FileNotFoundError as e:
        # Raise a clearer error with the resolved directory for debuggability
        raise FileNotFoundError(f"role profile not found: {full} (policy_dir={policy_dir})") from e

def _csv(header_val: Optional[str]) -> List[str]:
    if not header_val:
        return []
    return [t.strip() for t in str(header_val).split(",") if str(t).strip()]

def _min_sensitivity(a: str, b: Optional[str]) -> str:
    if not b:
        return a
    ia, ib = SENS_MAP.get((a or "low"), 1), SENS_MAP.get((b or "low"), 1)
    return a if ia <= ib else b

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

def compute_effective_policy(headers: Dict[str, str]) -> Dict[str, Any]:
    # Starlette/FastAPI provides headers in lowercase; normalise and fail-close.
    _require_headers(headers)
    h = _headers_lc(headers)

    user_id        = h["x-user-id"].strip()
    policy_version = h["x-policy-version"].strip()
    policy_key_hdr = h["x-policy-key"].strip()
    request_id     = h["x-request-id"].strip()
    trace_id       = h["x-trace-id"].strip()

    role = (h.get("x-user-roles") or "").split(",")[0].strip().lower()
    if not role:
        # Redundant because _require_headers enforces x-user-roles, but keep as guard.
        raise PolicyHeaderError("missing_required_headers:x-user-roles")

    role_profile = load_role_profile(role)

    # Namespaces: intersect requested with role-allowed (fail-closed semantics)
    user_namespaces = _csv(h.get("x-user-namespaces"))
    role_ns = [ns for ns in (role_profile.get("namespaces") or []) if isinstance(ns, str)]
    if role_ns:
        user_namespaces = [ns for ns in user_namespaces if ns in role_ns]

    # Scopes: prefer requested, else role default
    hdr_scopes = _csv(h.get("x-domain-scopes"))
    effective_scopes = hdr_scopes or (role_profile.get("domain_scopes") or [])

    # Edge allowlist: role baseline, optionally narrowed by header
    hdr_edges = _csv(h.get("x-edge-allow"))
    role_edges = [e for e in (role_profile.get("edge_allowlist") or []) if isinstance(e, str)]
    effective_edges = [e for e in role_edges if (not hdr_edges) or (e in hdr_edges)]

    eff_sens = _min_sensitivity(role_profile.get("sensitivity_ceiling", "low"),
                                h.get("x-sensitivity-ceiling"))
    try:
        max_hops = min(int(h.get("x-max-hops", "1") or "1"), 1)  # k=1 hard cap
    except Exception:
        max_hops = 1

    # Deterministic policy fingerprint for audit/replay (does NOT include runtime ids)
    fp_basis = {
        "role": role_profile.get("role") or role,
        "namespaces": sorted(user_namespaces),
        "scopes": sorted(effective_scopes),
        "edge_allowlist": sorted(effective_edges),
        "sensitivity": eff_sens,
        "max_hops": max_hops,
        "policy_version": policy_version,
    }
    computed_fp = "sha256:" + hashlib.sha256(json.dumps(fp_basis, sort_keys=True).encode("utf-8")).hexdigest()

    # If caller provided a policy key that doesn't match the fingerprint, log it.
    if policy_key_hdr and policy_key_hdr != computed_fp:
        log_stage(logger, "policy", "policy_key_mismatch", provided=policy_key_hdr, computed=computed_fp)

    return {
        "user_id": user_id,
        "role": role_profile.get("role") or role,
        "role_profile": role_profile,
        "namespaces": user_namespaces,
        "domain_scopes": effective_scopes,
        "edge_allowlist": effective_edges,
        "sensitivity_ceiling": eff_sens,
        "max_hops": max_hops,
        "policy_key": policy_key_hdr,     # from header (required)
        "policy_version": policy_version, # from header (required)
        "request_id": request_id,
        "trace_id": trace_id,
        "policy_fp": computed_fp,
    }

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
    node_sens = node.get("sensitivity") or "low"
    if SENS_MAP.get(node_sens, 1) > SENS_MAP.get(ceiling, 1):
        return False, "acl:sensitivity_exceeded"
    node_domain = node.get("domain") or ""
    if scopes and not _domain_in_scopes(node_domain, scopes):
        return False, "acl:domain_out_of_scope"
    return True, None

def field_mask(node: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    """Apply role-based field visibility; returns a shallow masked copy."""
    node = node or {}
    node_type = node.get("type")
    role_profile = policy.get("role_profile") or {}
    fv = (role_profile.get("field_visibility") or {}).get(node_type or "", {})
    visible = fv.get("visible_fields") or []
    out = {"id": node.get("_key") or node.get("id"), "type": node_type, "domain": node.get("domain")}
    if node_type == "decision":
        if "option" in visible:    out["option"] = node.get("option")
        if "timestamp" in visible: out["timestamp"] = node.get("timestamp")
        if "tags" in visible:      out["tags"] = node.get("tags")
        if "domain" in visible:    out["domain"] = node.get("domain")
        # rationale visibility can be bool or map with domain patterns and 'default'
        rv = fv.get("rationale_visible")
        allow_rationale = False
        if isinstance(rv, bool):
            allow_rationale = rv
        elif isinstance(rv, dict):
            dom = node.get("domain") or ""
            matched = False
            for key, val in rv.items():
                if key == "default":
                    continue
                if fnmatch.fnmatch(dom, key):
                    allow_rationale = bool(val)
                    matched = True
                    break
            if not matched and "default" in rv:
                allow_rationale = bool(rv["default"])
        if allow_rationale and node.get("rationale") is not None:
            out["rationale"] = node.get("rationale")
    elif node_type == "event":
        if "summary" in visible:     out["summary"] = node.get("summary")
        if "timestamp" in visible:   out["timestamp"] = node.get("timestamp")
        if "snippet" in visible:     out["snippet"]  = node.get("snippet")
        if "tags" in visible:        out["tags"]     = node.get("tags")
        if "domain" in visible:      out["domain"]   = node.get("domain")
        if "description" in visible and node.get("description"):
            out["description"] = node.get("description")
    else:
        out.update({k: node.get(k) for k in ("title","timestamp") if k in (node or {})})
    return out

def filter_and_mask_neighbors(neighbors: List[Dict[str, Any]], store, policy: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply edge allowlist + ACL + field masking to neighbor list."""
    included: List[Dict[str, Any]] = []
    withheld: Dict[str, str] = {}
    used_edge_types: set = set()
    for n in neighbors or []:
        nid = n.get("id")
        edge = n.get("edge") or {}
        etype = edge.get("type")
        if not _edge_allowed(etype, policy.get("edge_allowlist") or []):
            if nid:
                withheld[nid] = "edge_type_blocked"
            continue
        try:
            doc = store.get_node(nid) if hasattr(store, "get_node") else None
        except Exception:
            doc = None
            if nid:
                withheld[nid] = "acl:store_error"
            continue
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
        masked["edge"] = {"type": etype, "rel": edge.get("rel"), "direction": edge.get("direction"), "timestamp": edge.get("timestamp")}
        included.append(masked)
    policy_trace = {
        "withheld_ids": list(withheld.keys()),
        "reasons_by_id": withheld,
        "counts": {"hidden_vertices": len(withheld), "hidden_edges": len(withheld)},
        "edge_types_used": sorted(list(used_edge_types)),
    }
    return included, policy_trace
