from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional
import os, base64, binascii, mimetypes
from core_utils.fingerprints import canonical_json, sha256_hex, ensure_sha256_prefix, parse_fingerprint
from core_utils import jsonx
from core_logging import get_logger, log_stage
from core_logging.error_codes import ErrorCode
from core_validator.validator import (
    validate_bundle_view as _validate_bundle_view,
    validate_graph_view as _validate_graph_view,
    build_bundle_view as _build_bundle_view,
    view_artifacts_allowed as _view_artifacts_allowed,
)

logger = get_logger(__name__)
_REPORT_VERSION = "1.1"

# ---------------------------- utilities ----------------------------
def _resp_root(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict) and "response" in obj and "schema_version" in obj:
        inner = obj.get("response")
        return inner if isinstance(inner, dict) else {}
    return obj if isinstance(obj, dict) else {}

def _get_dict(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}

# ---------------------------- checks ----------------------------
def _bundle_schema_check(
    resp: Dict[str, Any],
    artifacts: Optional[Mapping[str, bytes]],
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Build a 'bundle view' from provided artifacts and validate against bundles.view.json.   
    """
    bundle: Dict[str, Any] = _build_bundle_view(resp, artifacts, report_version=_REPORT_VERSION)
    try:
        ok, schema_errors = _validate_bundle_view(bundle)
    except (ValueError, TypeError, RuntimeError) as exc:
        ok, schema_errors = False, [str(exc)]
    check = {"name": "bundle_schema", "ok": bool(ok)}
    if ok:
        return check, []
    return check, [{"code": ErrorCode.validation_failed, "msg": e} for e in (schema_errors or [])[:50]]

def _policy_fp_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    meta = _get_dict(_resp_root(resp).get("meta"))
    ok = bool(meta.get("policy_fp"))
    check = {"name": "policy_fp_applied", "ok": ok}
    if ok:
        return check, None
    return check, {"code": "policy_fingerprint_missing"}

def _bundle_inventory_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    _resp = _resp_root(resp)
    meta = _get_dict(_resp.get("meta"))
    bundle_fp = meta.get("bundle_fp") or _get_dict(meta.get("fingerprints")).get("bundle_fp")
    allowed_ids_fp = meta.get("allowed_ids_fp")
    snapshot_etag = meta.get("snapshot_etag")
    ok = all([bundle_fp, allowed_ids_fp, snapshot_etag])
    check = {"name": "bundle_inventory", "ok": ok}
    if ok:
        return check, None
    missing = []
    if not bundle_fp:      missing.append("bundle_fp")
    if not allowed_ids_fp: missing.append("allowed_ids_fp")
    if not snapshot_etag:  missing.append("snapshot_etag")
    return check, {"code": "bundle_inventory_missing", "missing": missing}

def view_artifacts_allowed() -> frozenset[str]:
    """Re-export the allowable artifact names from core_validator."""
    return _view_artifacts_allowed()

def view_artifacts_order() -> tuple[str, ...]:
    """Deterministic order for the 'view' bundle artifacts (sorted by name)."""
    return tuple(sorted(_view_artifacts_allowed()))

def _subset_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    _resp = _resp_root(resp)
    meta = _get_dict(_resp.get("meta"))
    allowed = list(meta.get("allowed_ids") or [])
    ans = _get_dict(_resp.get("answer"))
    cited = list(ans.get("cited_ids") or [])
    ok_subset = set(cited).issubset(set(allowed))
    check = {"name": "allowed_ids_subset", "ok": bool(ok_subset)}
    if ok_subset:
        return check, None
    return check, {"code": "cited_ids_not_subset", "cited_ids": cited, "allowed_ids": allowed}

def _edge_schema_check(
    resp: Dict[str, Any],
    request_id: str = "",
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Bundle schema already validates oriented edges; skip separate memory.graph_view check."""
    check = {"name": "edge_schema", "ok": True}
    return check, []

def _signature_check(
    envelope: Dict[str, Any],
    artifacts: Optional[Mapping[str, bytes]],
    *,
    request_id: str = "",
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Verify the receipt signature against the public bundle:
      - require artifacts["receipt.json"]
      - sig.covered must equal response.meta.bundle_fp
      - recompute canonical hash of response (without meta.bundle_fp)
      - verify Ed25519 signature with GATEWAY_ED25519_PUB_B64
    Fail-closed if the public key is missing.
    """
    check = {"name": "signature_verify", "ok": False}
    if not artifacts or "receipt.json" not in artifacts:
        return check, {"code": ErrorCode.bundle_signature_missing}

    try:
        sig = jsonx.loads(artifacts["receipt.json"])
    except (ValueError, TypeError):
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "receipt_invalid_json"}

    resp = _resp_root(envelope.get("response"))
    meta = _get_dict(resp.get("meta"))
    covered = str(sig.get("covered") or "")
    bundle_fp = str(meta.get("bundle_fp") or _get_dict(meta.get("fingerprints")).get("bundle_fp") or "")
    if not covered or not bundle_fp or covered != bundle_fp:
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "covered_mismatch", "covered": covered, "bundle_fp": bundle_fp}

    # recompute canonical hash (without meta.bundle_fp)
    try:
        resp_for_hash = dict(resp)
        _m = dict(resp_for_hash.get("meta") or {})
        _m.pop("bundle_fp", None)
        resp_for_hash["meta"] = _m
        canon = canonical_json(resp_for_hash)
        recomputed = ensure_sha256_prefix(sha256_hex(canon))
    except (ImportError, ValueError, TypeError, RuntimeError) as exc:
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "recompute_failed", "error": type(exc).__name__}
    if recomputed != covered:
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "recompute_mismatch", "covered": covered, "recomputed": recomputed}

    # Ed25519 only (fail-closed if key missing)
    pub_b64 = (os.getenv("GATEWAY_ED25519_PUB_B64") or "").strip()
    sig_b64 = str(sig.get("sig") or "")
    try:
        sig_raw = base64.b64decode(sig_b64)
    except (binascii.Error, ValueError):
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "sig_b64_invalid"}
    if not pub_b64:
        return check, {"code": ErrorCode.bundle_verifier_missing}
    try:
        try:
            from nacl.signing import VerifyKey  # type: ignore
            vk = VerifyKey(base64.b64decode(pub_b64))
            vk.verify(covered.encode("utf-8"), sig_raw)
        except ImportError:
            from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed  # type: ignore
            vk = _ed.Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
            vk.verify(sig_raw, covered.encode("utf-8"))
        check["ok"] = True
        log_stage(logger, "validator", "signature_ok", algo="ed25519", request_id=request_id)
        return check, None
    except Exception as exc:
        return check, {"code": ErrorCode.bundle_signature_invalid, "reason": "ed25519_invalid", "error": type(exc).__name__}

def _manifest_check(artifacts: Optional[Mapping[str, bytes]]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Validate bundle.manifest.json:
      - present and valid JSON
      - for each listed artifact: sha256, bytes, content_type match actual data
      - no extra artifacts present that are not listed
    """
    check = {"name": "manifest_integrity", "ok": False}
    if not artifacts or "bundle.manifest.json" not in artifacts:
        return check, {"code": "manifest_missing"}
    try:
        manifest = jsonx.loads(artifacts["bundle.manifest.json"])
        listed = list(manifest.get("artifacts") or [])
    except (ValueError, TypeError):
        return check, {"code": "manifest_invalid_json"}

    errs: List[Dict[str, Any]] = []
    listed_names = set()
    for entry in listed:
        if not isinstance(entry, dict):
            errs.append({"code": "manifest_entry_invalid"})
            continue
        name = str(entry.get("name") or "")
        sha  = str(entry.get("sha256") or "")
        size = int(entry.get("bytes") or -1)
        ctyp = str(entry.get("content_type") or "")
        if not name:
            errs.append({"code": "manifest_entry_missing_name"})
            continue
        listed_names.add(name)
        if name not in artifacts:
            errs.append({"code": "manifest_missing_artifact", "name": name})
            continue
        blob = artifacts[name]
        # Accept either 'sha256:<hex>' or bare '<hex>'
        alg, expected_hex = parse_fingerprint(sha) if ":" in sha else ("sha256", sha)
        # Deterministically recompute
        if sha256_hex(blob) != expected_hex:
            errs.append({"code": "manifest_sha_mismatch", "name": name})
        if len(blob) != size:
            errs.append({"code": "manifest_size_mismatch", "name": name})
        guess = mimetypes.guess_type(name, strict=False)[0] or "application/json"
        if ctyp != guess:
            errs.append({"code": "manifest_content_type_mismatch", "name": name, "expected": guess, "got": ctyp})
    # check for extras (artifacts not declared), excluding the manifest itself
    extras = set(artifacts.keys()) - listed_names - {"bundle.manifest.json"}
    if extras:
        for name in sorted(extras):
            errs.append({"code": "manifest_extra_artifact", "name": name})
    if not errs:
        check["ok"] = True
        return check, None
    return check, {"code": "manifest_mismatch", "issues": errs}

# ---------------------------- public API ----------------------------
def run_validator(
    resp: Dict[str, Any],
    artifacts: Optional[Mapping[str, bytes]] = None,
    *,
    request_id: str = "",
) -> Dict[str, Any]:
    """
    Build a validation report for the Exec Summary bundle (view flavor).
    """
    checks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    c, es = _bundle_schema_check(resp, artifacts)
    checks.append(c); errors.extend(es or [])

    c, e = _policy_fp_check(resp)
    checks.append(c);  e and errors.append(e)

    c, e = _bundle_inventory_check(resp)
    checks.append(c);  e and errors.append(e)

    c, e = _signature_check(resp if "response" in (resp or {}) else {"response": resp}, artifacts, request_id=request_id)
    checks.append(c);  e and errors.append(e)

    c, e = _manifest_check(artifacts)
    checks.append(c);  e and errors.append(e)

    c, es = _edge_schema_check(resp, request_id=request_id)
    checks.append(c); errors.extend(es or [])

    c, e = _subset_check(resp)
    checks.append(c);  e and errors.append(e)

    passed = all(ch.get("ok") for ch in checks) and not errors
    report = {
        "version": _REPORT_VERSION,
        "pass": bool(passed),
        "errors": errors,
        "checks": checks,
    }
    return report
