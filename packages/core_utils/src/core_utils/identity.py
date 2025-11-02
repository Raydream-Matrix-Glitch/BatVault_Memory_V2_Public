from __future__ import annotations
from typing import Dict, Any, List, Optional, Mapping
from core_http.headers import (
    X_USER_ID, X_USER_EMAIL, X_USER_ROLES,
    X_ORG_ID, X_TENANT_ID,
)

def _headers_lc(headers: Mapping[str, str] | Dict[str, str]) -> Dict[str, str]:
    """Lowercase header keys; values unchanged."""
    return { (str(k).lower() if isinstance(k, str) else k): v for k, v in (headers or {}).items() }

def identity_from_headers(headers: Dict[str, str]) -> Dict[str, Any]:
    """Normalise identity fields from request headers.
    Accepts both lower- and upper-cased names. Roles are comma-separated.
    Emits both `org_id` and `tenant_id` (no implicit fallback in output).
    """
    h = _headers_lc(headers)
    k_uid   = X_USER_ID.lower()
    k_mail  = X_USER_EMAIL.lower()
    k_roles = X_USER_ROLES.lower()
    k_org   = X_ORG_ID.lower()
    k_ten   = X_TENANT_ID.lower()

    roles_raw = [r.strip() for r in str(h.get(k_roles) or "").split(",") if r.strip()]
    # Deterministic role order for all consumers (no duplicates)
    roles = sorted(dict.fromkeys(roles_raw))
    return {
        "user_id":   str(h.get(k_uid)  or "").strip(),
        "email":     str(h.get(k_mail) or "").strip(),
        "org_id":    str(h.get(k_org)  or "").strip(),
        "tenant_id": str(h.get(k_ten)  or "").strip(),
        "roles":   roles,
    }

def build_opa_input(
    *,
    anchor_id: str,
    edges: List[Dict[str, Any]],
    headers: Dict[str, str],
    snapshot_etag: str,
    intents: Optional[List[str]] = None,
    include_headers: bool = True,
) -> Dict[str, Any]:
    """Construct the canonical OPA input envelope.
    Deterministic where applicable: roles are already de-duplicated & sorted; intents default to ["enrich"].
    """
    ident = identity_from_headers(headers)
    input_obj: Dict[str, Any] = {
        "identity": ident,
        "resource": {"anchor_id": anchor_id},
        "intents": list(intents or ["enrich"]),
        "edges": edges,
        "snapshot_etag": snapshot_etag,
    }
    if include_headers:
        input_obj["headers"] = headers
    return input_obj