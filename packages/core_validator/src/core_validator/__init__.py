"""
Public API for the core_validator package.

This module exposes the key validation entry points.  Consumers should
import functions from here rather than pulling private helpers from
``core_validator.validator`` directly.  The ``canonical_allowed_ids``
function computes a stable ordering of anchor, event and transition IDs
according to the Why‑Decision contract.
"""

from .validator import validate_response, canonical_allowed_ids  # noqa: F401

__all__ = [
    "validate_response",
    "canonical_allowed_ids",
]