import os, json, fnmatch, hashlib
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from core_logging import get_logger, log_stage, log_once
from core_config import get_settings
from core_utils.domain import storage_key_to_anchor, is_valid_anchor

logger = get_logger("memory_api")

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
    """Canonical fingerprint: sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).utf8)."""
    try:
        payload = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()
    except (TypeError, ValueError, UnicodeEncodeError):
        # Never raise on fingerprinting
        return "sha256:unknown"

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
            request_id=(h.get("x-request-id") or ""),
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

def _sens_rank(level: Optional[str]) -> int:
    order = _sensitivity_order()
    try:
        return 1 + order.index((level or "low"))
    except (ValueError, AttributeError):
        return 1  # default to least restrictive

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
    try:
        role_profile = load_role_profile(role)
    except FileNotFoundError as e:
        # Fail-closed: unknown/unsupported role — treat as policy error (400) upstream
        log_stage(logger, "policy", "role_not_found", role=role)
        raise PolicyHeaderError("unknown_role") from e

    # Denial status toggle (default 403; allow header override to 404)
    try:
        _ds = str((h.get("x-denied-status") or "").strip())
        denied_status = 404 if _ds == "404" else 403
    except (ValueError, TypeError):
        denied_status = 403

    # Extra field visibility for x-extra (default deny)
    extra_visible = role_profile.get("extra_visible") or []

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

    # ---- ALIAS behaviour knobs (policy-driven) --------------------------------
    # Allow ALIAS projection if role allows it (policy-owned; request header only toggles follow on/off).
    allow_alias = bool(((role_profile.get("field_visibility") or {}).get("event") or {}).get("allow_alias_projection", False))
    follow_alias_hdr = (h.get("x-follow-alias") or "").strip().lower() in {"1","true","yes","on"}
    # Maximum hops is clamped by role profile (alias_max_hops); header may request fewer but never more.
    try:
        role_alias_max = int((role_profile.get("alias_max_hops") or 3))
    except (ValueError, TypeError):
        role_alias_max = 3
    try:
        alias_hdr_val = int(h.get("x-alias-hops", str(role_alias_max)) or role_alias_max)
    except (ValueError, TypeError):
        alias_hdr_val = role_alias_max
    alias_hops = max(0, min(alias_hdr_val, role_alias_max))

    # Deterministic policy fingerprint for audit/replay (does NOT include runtime ids)
    # Include a stable hash of field_visibility so policy_fp changes if mask patterns change.
    try:
        _fv_cfg = (role_profile.get("field_visibility") or {})
        _fv_bytes = json.dumps(_fv_cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _fv_hash = hashlib.sha256(_fv_bytes).hexdigest()
    except (TypeError, ValueError, UnicodeEncodeError):
        _fv_hash = "unknown"

    fp_basis = {
        "role": role_profile.get("role") or role,
        "namespaces": sorted(user_namespaces),
        "scopes": sorted(effective_scopes),
        "edge_allowlist": sorted(effective_edges),
        "sensitivity": eff_sens,
        "max_hops": max_hops,
        "policy_version": policy_version,
        "extra_visible": extra_visible,
        "fv_hash": _fv_hash,
    }
    computed_fp = policy_fingerprint(fp_basis)
    # Defer single fingerprint emission to the response assembly path.

    # Only warn when a caller asserts a fingerprint that truly disagrees.
    if policy_key_hdr and policy_key_hdr != computed_fp:
        if isinstance(policy_key_hdr, str) and policy_key_hdr.startswith("sha256:"):
            # Explicit mismatch is an error-like event -> keep as an immediate log
            log_stage(logger, "policy", "policy_fp_mismatch",
                      provided_fp=policy_key_hdr, computed_fp=computed_fp, request_id=request_id)
        else:
            # Human-readable keys: emit the computed fingerprint once per request (no spam)
            log_once(logger, key="policy_fp", event="policy_fp", stage="policy",
                     computed_fp=computed_fp, request_id=request_id)

    return {
        "user_id": user_id,
        "role": role_profile.get("role") or role,
        "role_profile": role_profile,
        "namespaces": user_namespaces,
        "domain_scopes": effective_scopes,
        "edge_allowlist": effective_edges,
        "sensitivity_ceiling": eff_sens,
        "max_hops": max_hops,
        "allow_alias": allow_alias,
        "follow_alias": follow_alias_hdr,
        "alias_hops": alias_hops,
        "alias_max_hops": role_alias_max,
        "policy_key": policy_key_hdr,     # from header (required)
        "policy_version": policy_version, # from header (required)
        "request_id": request_id,
        "trace_id": trace_id,
        "policy_fp": computed_fp,
        "denied_status": denied_status,
        "extra_visible": extra_visible,
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
    node_type_lc = node_type_raw.lower()
    node_type_uc = node_type_raw.upper()
    role_profile = policy.get("role_profile") or {}
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
                          frm=_raw_id, to=_wire_id, request_id=(policy or {}).get("request_id"))
            except (RuntimeError, ValueError, TypeError):
                pass
    elif isinstance(_raw_id, str) and _raw_id:
        # Fall back: accept storage-style id and convert to wire form deterministically.
        _wire_id = storage_key_to_anchor(_raw_id)
        if _raw_id != _wire_id:
            try:
                log_stage(logger, "policy", "id_normalized",
                          frm=_raw_id, to=_wire_id, request_id=(policy or {}).get("request_id"))
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
    rationale_visible = fv.get("rationale_visible")
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
    except Exception:
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
