from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional, Tuple
from functools import lru_cache
from pathlib import Path
import os, json
from core_utils import jsonx
from core_logging import get_logger, log_stage
from core_validator.validator import (
    validate_bundle_view as _validate_bundle_view,
    validate_graph_view as _validate_graph_view,
)
import core_models
import os, json, base64, binascii

logger = get_logger(__name__)
_REPORT_VERSION = "1.1"

def _schemas_dir() -> Path:
    env = os.getenv("BATVAULT_SCHEMAS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        # allow both <dir>/bundles.view.json and <dir>/schemas/bundles.view.json
        if (p / "bundles.view.json").exists():
            return p
        if (p / "schemas" / "bundles.view.json").exists():
            return p / "schemas"
    # fallback to packaged schemas
    return Path(core_models.__file__).parent / "schemas"

@lru_cache(maxsize=1)
def _view_schema() -> dict:
    with (_schemas_dir() / "bundles.view.json").open("r", encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def view_artifacts_allowed() -> frozenset[str]:
    props = (_view_schema().get("properties") or {})
    return frozenset(props.keys()) if props else frozenset()

@lru_cache(maxsize=1)
def view_artifacts_order() -> tuple[str, ...]:
    req = tuple(_view_schema().get("required") or ())
    # if schema has no explicit "required" ordering, provide stable sorted order
    return req or tuple(sorted(view_artifacts_allowed()))

def _resp_root(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Return response root; unwrap envelope if shape is {schema_version, response, signature}."""
    if isinstance(obj, dict) and "response" in obj and "schema_version" in obj:
        inner = obj.get("response")
        return inner if isinstance(inner, dict) else {}
    return obj if isinstance(obj, dict) else {}

def _get_dict(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}

def _subset_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    allowed_ids_subset: cited_ids ⊆ evidence.allowed_ids
    Returns (check_record, error_record_or_none).
    """
    _resp = _resp_root(resp)
    # Public v3 response doesn’t carry 'evidence' – meta.allowed_ids is authoritative.
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
    artifacts: Optional[Mapping[str, bytes]],
    *,
    request_id: str = "",
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    edge_schema: delegate to core_validator.validate_bundle_view on a bundle-view dict:
      { "response.json": <sanitized resp>, ...artifact_jsons }
    Returns (check_record, list_of_error_records).
    """
    bundle: Dict[str, Any] = {}
    allowed = view_artifacts_allowed()
    # Load provided artifacts into the bundle view (allowed set only).
    if artifacts:
        for name, raw in artifacts.items():
            if name not in allowed or name == "response.json":
                continue
            try:
                bundle[name] = jsonx.loads(raw)
            except (ValueError, TypeError):
                bundle[name] = {}
    # Prefer the serialized artifact (enveloped) when available; otherwise sanitize resp
    if artifacts and "response.json" in artifacts:
        try:
            bundle["response.json"] = jsonx.loads(artifacts["response.json"])
        except (ValueError, TypeError):
            bundle["response.json"] = jsonx.sanitize(resp)
    else:
        bundle["response.json"] = jsonx.sanitize(resp)
    if "validator_report.json" in allowed and "validator_report.json" not in bundle:
        bundle["validator_report.json"] = {
            "version": _REPORT_VERSION,
            "pass": True,
            "errors": [],
            "checks": [],
        }
    try:
        ok, schema_errors = _validate_bundle_view(bundle)
    except (ValueError, TypeError, RuntimeError) as exc:
        ok, schema_errors = False, [str(exc)]
    check = {"name": "bundle_schema", "ok": bool(ok)}
    if ok:
        return check, []
    return check, [{"code": "schema_error", "msg": e} for e in (schema_errors or [])[:50]]

def _policy_fp_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """policy_fp_applied: require meta.policy_fp (Memory is authoritative)."""
    meta = _get_dict(_resp_root(resp).get("meta"))
    policy_fp = meta.get("policy_fp")
    ok = bool(policy_fp)
    check = {"name": "policy_fp_applied", "ok": ok}
    if ok:
        return check, None
    return check, {"code": "policy_fingerprint_missing"}


def _bundle_inventory_check(resp: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """bundle_inventory: require response.meta.{bundle_fp, allowed_ids_fp, snapshot_etag} (no fallbacks)."""
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

def _signature_check(
    envelope: Dict[str, Any],
    *,
    request_id: str = "",
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Verify the signature block on an Exec Summary envelope.
    Rules:
      - If `signature` is absent → soft pass with `skipped: True` (schema may still allow it).
      - `signature.covered` must equal response.meta.bundle_fp.
      - Recomputed bundle_fp over canonical response must equal `signature.covered`.
      - If a verifier is configured:
          • GATEWAY_ED25519_PUB_B64 → ed25519 verify
          • else GATEWAY_HMAC_SHA256_KEY_B64 → HMAC-SHA256 verify
        Log `signature_ok` or `signature_invalid` with deterministic fields.
    """
    check = {"name": "signature_verify", "ok": False}
    sig = _get_dict(envelope.get("signature"))
    if not sig:
        check["ok"] = True
        check["skipped"] = True
        log_stage(logger, "validator", "signature_skipped", request_id=request_id)
        return check, None

    # Unwrap response root & extract bundle_fp
    resp = _resp_root(envelope.get("response"))
    meta = _get_dict(resp.get("meta"))
    covered = str(sig.get("covered") or "")
    bundle_fp = str(meta.get("bundle_fp") or _get_dict(meta.get("fingerprints")).get("bundle_fp") or "")
    if not covered or not bundle_fp or covered != bundle_fp:
        return check, {"code": "signature_mismatch_bundle_fp", "covered": covered, "bundle_fp": bundle_fp}

    # Recompute bundle_fp from canonical response JSON
    try:
        from core_utils.fingerprints import canonical_json
        import hashlib as _hl
        # Recompute over the response *without* meta.bundle_fp:
        resp_for_hash = dict(resp)
        _m = dict(resp_for_hash.get("meta") or {})
        _m.pop("bundle_fp", None)
        resp_for_hash["meta"] = _m
        canon = canonical_json(resp_for_hash)
        recomputed = "sha256:" + _hl.sha256(canon).hexdigest()
    except (ImportError, ValueError, TypeError, RuntimeError) as exc:
        return check, {"code": "signature_recompute_failed", "error": type(exc).__name__}
    if recomputed != covered:
        return check, {"code": "signature_recompute_mismatch", "covered": covered, "recomputed": recomputed}

    # Cryptographic verification (ed25519 preferred, HMAC fallback)
    import hmac, hashlib as _hash
    pub_b64 = os.getenv("GATEWAY_ED25519_PUB_B64")
    mac_b64 = os.getenv("GATEWAY_HMAC_SHA256_KEY_B64")
    sig_b64 = str(sig.get("sig") or "")
    try:
        sig_raw = base64.b64decode(sig_b64)
    except (binascii.Error, ValueError):
        return check, {"code": "signature_b64_invalid"}

    if pub_b64:
        try:
            # Prefer PyNaCl if available (explicit error class), otherwise cryptography.
            try:
                from nacl.signing import VerifyKey  # type: ignore
                from nacl.exceptions import BadSignatureError  # type: ignore
                vk = VerifyKey(base64.b64decode(pub_b64))
                try:
                    vk.verify(covered.encode("utf-8"), sig_raw)
                    check["ok"] = True
                    log_stage(logger, "validator", "signature_ok", algo="ed25519", request_id=request_id)
                    return check, None
                except BadSignatureError:
                    logger.error("signature_invalid", extra={"algo": "ed25519", "request_id": request_id, "stage": "validator"})
                    return check, {"code": "signature_ed25519_invalid", "error": "BadSignatureError"}
            except ImportError:
                from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
                from cryptography.exceptions import InvalidSignature  # type: ignore
                vk = _ed.Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
                try:
                    vk.verify(sig_raw, covered.encode("utf-8"))
                    check["ok"] = True
                    log_stage(logger, "validator", "signature_ok", algo="ed25519", request_id=request_id)
                    return check, None
                except InvalidSignature:
                    logger.error("signature_invalid", extra={"algo": "ed25519", "request_id": request_id, "stage": "validator"})
                    return check, {"code": "signature_ed25519_invalid", "error": "InvalidSignature"}
        except (ValueError, TypeError, binascii.Error) as exc:
            return check, {"code": "signature_ed25519_invalid", "error": type(exc).__name__}

    if mac_b64:
        try:
            key = base64.b64decode(mac_b64)
            expected = hmac.new(key, covered.encode("utf-8"), _hash.sha256).digest()
            if hmac.compare_digest(expected, sig_raw):
                check["ok"] = True
                log_stage(logger, "validator", "signature_ok", algo="hmac-sha256", request_id=request_id)
                return check, None
            logger.error("signature_invalid", extra={"algo": "hmac-sha256", "request_id": request_id, "stage": "validator"})
            return check, {"code": "signature_hmac_invalid"}
        except (binascii.Error, ValueError, TypeError) as exc:
            return check, {"code": "signature_hmac_error", "error": type(exc).__name__}

    # No verifier configured — treat as verified but skipped cryptographic check.
    check["ok"] = True
    check["skipped"] = True
    log_stage(logger, "validator", "signature_skipped", request_id=request_id)
    return check, None

def run_memory_view_validator(
    view: Any,
    *,
    request_id: str = "",
) -> Dict[str, Any]:
    """
    Thin, Gateway-local report for validating Memory's edges-only graph view.
    Delegates schema checks to core_validator.validate_graph_view.
    Returns { version, pass, errors, checks } aligning with run_validator().
    """
    checks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # 1) graph_view_schema (delegate to core authority; narrow exception scope)
    ok = False
    schema_errors: List[str] = []
    try:
        result = _validate_graph_view(view)
        if isinstance(result, tuple) and len(result) == 2:
            ok, schema_errors = bool(result[0]), list(result[1] or [])
        else:
            ok, schema_errors = True, []
    except (ValueError, TypeError, RuntimeError) as exc:
        ok, schema_errors = False, [str(exc)]
        logger.error("memory_view_validator_exception", extra={"request_id": request_id, "error": type(exc).__name__, "stage": "validator"})
    checks.append({"name": "graph_view_schema", "ok": bool(ok)})
    if not ok:
        errors.extend([{"code": "schema_error", "msg": e} for e in (schema_errors or [])[:50]])

    passed = all(c.get("ok") for c in checks)
    report = {
        "version": _REPORT_VERSION,
        "pass": bool(passed),
        "errors": errors,
        "checks": checks,
    }
    log_stage(logger, "validator", "memory_view_validator_result",
              request_id=request_id, passed=bool(passed), checks_total=len(checks), errors_total=len(errors))
    return report

def run_validator(
    resp: Any,
    artifacts: Optional[Mapping[str, bytes]] = None,
    *,
    request_id: str = "",
) -> Dict[str, Any]:
    """
    Thin Stage-7 report builder.
    - Delegates schema/timestamp/orientation rules to core_validator.validate_bundle_view
    - Keeps Gateway free of duplicate regexes and shape logic.

    Returns a dict:
      {
        "version": "1.1",
        "pass": bool,
        "errors": [ {code, ...}, ... ],
        "checks": [ {name, ok}, ... ]
      }
    """
    checks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # Ensure dict input for downstream checks.
    response_dict: Dict[str, Any] = resp if isinstance(resp, dict) else _get_dict(getattr(resp, "__dict__", {}))

    # 1) allowed_ids_subset
    c, e = _subset_check(response_dict)
    checks.append(c)
    if e:
        errors.append(e)

    # 2) edge_schema (via core authority)
    c, elist = _edge_schema_check(response_dict, artifacts, request_id=request_id)
    checks.append(c)
    if elist:
        errors.extend(elist)

    # 3) policy_fp_applied
    c, e = _policy_fp_check(response_dict)
    checks.append(c)
    if e:
        errors.append(e)

    # 4) bundle_inventory
    c, e = _bundle_inventory_check(response_dict)
    checks.append(c)
    if e:
        errors.append(e)

    # 5) signature_verify
    c, e = _signature_check(response_dict, request_id=request_id)
    checks.append(c)
    if e:
        errors.append(e)

    passed = all(c.get("ok") for c in checks)

    report = {
        "version": _REPORT_VERSION,
        "pass": bool(passed),
        "errors": errors,
        "checks": checks,
    }

    # Structured, context-rich log
    log_stage(
        logger, "validator", "bundle_validator_result",
        request_id=request_id,
        passed=bool(passed),
        checks_total=len(checks),
        errors_total=len(errors),
    )
    return report

__all__ = ["run_validator", "run_memory_view_validator"]
