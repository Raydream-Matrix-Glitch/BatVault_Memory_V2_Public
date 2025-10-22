from enum import Enum

class ErrorCode(str, Enum):
    # Policy / OPA
    OPA_DENY             = "OPA.DENY"
    OPA_ERROR            = "OPA.ERROR"

    # Validation / Contracts
    VALIDATION_FAILED    = "VALIDATION.FAILED"
    CONTRACT_VIOLATION   = "CONTRACT.VIOLATION"

    # Upstream / Networking
    UPSTREAM_TIMEOUT     = "UPSTREAM.TIMEOUT"
    UPSTREAM_ERROR       = "UPSTREAM.ERROR"

    # Storage / Object store
    BUNDLE_SIGNATURE_MISSING = "BUNDLE.SIGNATURE_MISSING"
    STORAGE_UNAVAILABLE      = "STORAGE.UNAVAILABLE"
    STORAGE_TIMEOUT          = "STORAGE.TIMEOUT"

    # Cache
    CACHE_UNAVAILABLE    = "CACHE.UNAVAILABLE"

    # Internal
    INTERNAL             = "INTERNAL"

__all__ = ["ErrorCode"]