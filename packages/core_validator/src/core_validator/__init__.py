"""
Public API for the core_validator package (v3).

Strict JSON Schema validators for BatVault contracts.
Import from here in services (ingest/memory_api/gateway) to avoid drift.
"""

from .validator import (  # noqa: F401
    validate_node,
    validate_edge,
    validate_graph_view,
    validate_bundle_view,
)

__all__ = [
    "validate_node",
    "validate_edge",
    "validate_graph_view",
    "validate_bundle_view",
]