"""
Canonical HTTP header names used across BatVault services.
"""
from typing import Final, Mapping, MutableMapping, Dict

# --- Snapshot-bound invariants -------------------------------------------------
REQUEST_SNAPSHOT_ETAG: Final[str]   = "X-Snapshot-ETag"   # clients MUST send on reads
RESPONSE_SNAPSHOT_ETAG: Final[str]  = "x-snapshot-etag"   # mirrored on responses

# --- Audit / policy fingerprints (mirrored on responses) -----------------------
BV_POLICY_FP: Final[str]            = "X-BV-Policy-Fingerprint"
BV_ALLOWED_IDS_FP: Final[str]       = "X-BV-Allowed-Ids-FP"
BV_GRAPH_FP: Final[str]             = "X-BV-Graph-FP"
BV_POLICY_ENGINE_FP: Final[str]     = "X-BV-Policy-Engine-FP"  # telemetry-only (OPA), never used for caches/gates

# --- Identity / Policy input headers (canonical casing) ------------------------
X_USER_ID: Final[str]               = "X-User-Id"
X_USER_EMAIL: Final[str]            = "X-User-Email"
X_USER_ROLES: Final[str]            = "X-User-Roles"
X_USER_NAMESPACES: Final[str]       = "X-User-Namespaces"
X_POLICY_VERSION: Final[str]        = "X-Policy-Version"
X_POLICY_KEY: Final[str]            = "X-Policy-Key"
X_DOMAIN_SCOPES: Final[str]         = "X-Domain-Scopes"
X_EDGE_ALLOW: Final[str]            = "X-Edge-Allow"
X_MAX_HOPS: Final[str]              = "X-Max-Hops"
X_SENSITIVITY_CEILING: Final[str]   = "X-Sensitivity-Ceiling"
X_EXTRA_ALLOW: Final[str]           = "X-Extra-Allow"
X_ORG_ID: Final[str]                = "X-Org-Id"
X_TENANT_ID: Final[str]             = "X-Tenant-Id"
X_DENIED_STATUS: Final[str]         = "X-Denied-Status"   # optional: 403/404 selection

# Required/optional sets for validators (lower-cased for case-insensitive lookups).
# Keep small and explicit; Memory API is fail-closed on these.
REQUIRED_POLICY_HEADERS_LOWER = [
    "x-user-id",
    "x-user-roles",
    "x-policy-version",
    "x-policy-key",
]
OPTIONAL_POLICY_HEADERS_LOWER = [
    "x-user-namespaces",
    "x-domain-scopes",
    "x-edge-allow",
    "x-max-hops",
    "x-sensitivity-ceiling",
    "x-extra-allow",
    "x-user-email",
    "x-org-id",
    "x-tenant-id",
    "x-denied-status",
]

# Headers the Gateway should forward to Memory (includes cache/fp hints).
FORWARDABLE_POLICY_HEADERS = [
    X_USER_ID, X_USER_ROLES, X_USER_NAMESPACES,
    X_POLICY_VERSION, X_POLICY_KEY, X_DOMAIN_SCOPES,
    X_EDGE_ALLOW, X_MAX_HOPS, X_SENSITIVITY_CEILING, X_EXTRA_ALLOW,
    X_USER_EMAIL, X_ORG_ID, X_TENANT_ID, X_DENIED_STATUS,
    REQUEST_SNAPSHOT_ETAG, BV_POLICY_FP, BV_ALLOWED_IDS_FP,
]

def extract_policy_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    """
    Case-insensitive selection of policy/identity/cache headers with canonical casing.
    Strips empty values. Intended for pass-through between services.
    """
    src = {str(k).lower(): v for k, v in headers.items()}
    out: Dict[str, str] = {}
    for name in FORWARDABLE_POLICY_HEADERS:
        v = src.get(name.lower())
        if isinstance(v, str) and v.strip():
            out[name] = v.strip()
    return out

# Standard cache headers we use explicitly
ETAG: Final[str] = "ETag"
IF_NONE_MATCH: Final[str] = "If-None-Match"

__all__ = [
    "REQUEST_SNAPSHOT_ETAG",
    "RESPONSE_SNAPSHOT_ETAG",
    "BV_POLICY_FP",
    "BV_ALLOWED_IDS_FP",
    "BV_GRAPH_FP",
    "ETAG",
    "BV_POLICY_ENGINE_FP",
    "IF_NONE_MATCH",
    # Canonical policy/identity headers + sets/helpers
    "X_USER_ID",
    "X_USER_EMAIL",
    "X_USER_ROLES",
    "X_USER_NAMESPACES",
    "X_POLICY_VERSION",
    "X_POLICY_KEY",
    "X_DOMAIN_SCOPES",
    "X_EDGE_ALLOW",
    "X_MAX_HOPS",
    "X_SENSITIVITY_CEILING",
    "X_EXTRA_ALLOW",
    "X_ORG_ID",
    "X_TENANT_ID",
    "X_DENIED_STATUS",
    "REQUIRED_POLICY_HEADERS_LOWER",
    "OPTIONAL_POLICY_HEADERS_LOWER",
    "FORWARDABLE_POLICY_HEADERS",
    "extract_policy_headers",
    "mirror_snapshot_headers",
]

def mirror_snapshot_headers(
    headers: MutableMapping[str, str],
    *,
    snapshot_etag: str | None,
    policy_fp: str | None = None,
    allowed_ids_fp: str | None = None,
    graph_fp: str | None = None,
) -> None:
    """
    Write standard snapshot/audit headers to a response-like mapping.
    - Always mirror x-snapshot-etag.
    - Mirror X-BV-Policy-Fingerprint / X-BV-Allowed-Ids-FP when provided.
    - Mirror X-BV-Graph-FP **only** for graph responses (i.e., when the payload contains meta.fingerprints.graph_fp).
      Node-only enrich responses must not set X-BV-Graph-FP.
    Keeps naming & casing consistent across services.
    """
    if snapshot_etag:
        headers[RESPONSE_SNAPSHOT_ETAG] = str(snapshot_etag)
    if policy_fp:
        headers[BV_POLICY_FP] = str(policy_fp)
    if allowed_ids_fp:
        headers[BV_ALLOWED_IDS_FP] = str(allowed_ids_fp)
    if graph_fp:
        headers[BV_GRAPH_FP] = str(graph_fp)