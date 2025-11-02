from enum import Enum

class ErrorCode(str, Enum):
    """
    Canonical error codes for the public error envelope.
    Mirrors https://batvault.dev/schemas/error.json
    """
    precondition_failed       = "precondition_failed"
    policy_denied             = "policy_denied"
    invalid_edge              = "invalid_edge"
    validation_failed         = "validation_failed"
    contract_violation        = "contract_violation"
    opa_error                 = "opa_error"
    internal                  = "internal"
    upstream_timeout          = "upstream_timeout"
    upstream_error            = "upstream_error"
    storage_unavailable       = "storage_unavailable"
    storage_timeout           = "storage_timeout"
    cache_unavailable         = "cache_unavailable"
    bundle_signature_missing  = "bundle_signature_missing"
    bundle_signature_invalid  = "bundle_signature_invalid"
    bundle_verifier_missing   = "bundle_verifier_missing"

__all__ = ["ErrorCode"]