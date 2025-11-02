from __future__ import annotations
import base64, os
from datetime import datetime, timezone
from typing import Dict, Any
from core_logging import get_logger, log_stage
from core_utils.fingerprints import canonical_json, sha256_hex, ensure_sha256_prefix

logger = get_logger("gateway")

class SigningError(RuntimeError):
    pass

def _ed25519_backend_available() -> bool:
    try:
        import nacl.signing  # type: ignore
        return True
    except ImportError:
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
            return True
        except ImportError:
            return False

def _load_priv_b64() -> str:
    """
    Deterministically load the Ed25519 seed (base64 of 32 bytes) from env only.
    This hardens the setup: if the key isn't in the environment, we fail closed.
    """
    return (os.getenv("GATEWAY_ED25519_PRIV_B64") or "").strip()

def select_and_sign(response_obj: Dict[str, Any], *, request_id: str) -> Dict[str, Any]:
    """
    Deterministically select a signing algorithm and produce the signature block.
    Rules (v3):
      - Ed25519 only, enabled when a valid base64 seed is provided AND a backend is available.
      - Else, fail-closed with no signer configured.
    We sign the canonical bytes of the response WITHOUT meta.bundle_fp, then mirror the
    resulting 'covered' (sha256 hex) into meta.bundle_fp upstream.
    """
    priv_b64 = _load_priv_b64()

    # Compute 'covered' deterministically
    resp_for_hash = dict(response_obj)
    _m = dict(resp_for_hash.get("meta") or {})
    _m.pop("bundle_fp", None)
    resp_for_hash["meta"] = _m
    canon = canonical_json(resp_for_hash)
    covered = ensure_sha256_prefix(sha256_hex(canon))

    # Decide signer once, without try-and-fallback noise
    key_id = (os.getenv("GATEWAY_SIGN_KEY_ID") or "gateway/k1").strip()
    if priv_b64 and _ed25519_backend_available():
        try:
            raw = base64.b64decode(priv_b64)
            if len(raw) != 32:
                log_stage(logger, "signing", "invalid_key_length", request_id=request_id, got=len(raw))
                raise SigningError("invalid_signing_key")
            try:
                from nacl.signing import SigningKey  # type: ignore
                sig_raw = SigningKey(raw).sign(covered.encode("utf-8")).signature
            except ImportError:
                from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
                sig_raw = _ed.Ed25519PrivateKey.from_private_bytes(raw).sign(covered.encode("utf-8"))
            log_stage(logger, "signing", "algo_selected", request_id=request_id, algo="ed25519")
            return {
                "alg": "ed25519",
                "key_id": key_id,
                "sig": base64.b64encode(sig_raw).decode("ascii"),
                "covered": covered,
                "signed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"),
            }
        except (ValueError, TypeError, base64.binascii.Error) as exc:
            # Ed25519 explicitly configured but unusable: fail-closed.
            log_stage(logger, "signing", "algo_unavailable", request_id=request_id, algo="ed25519", error=type(exc).__name__)
            # Bubble specific invalid key up; otherwise keep deterministic 'no_signer_configured'
            if isinstance(exc, SigningError):
                raise
            raise SigningError("no_signer_configured") from exc

    # Nothing configured → explicit, deterministic failure
    log_stage(logger, "signing", "signing.signature_skipped",
              request_id=request_id, reason="no_signer_configured")
    raise SigningError("no_signer_configured")