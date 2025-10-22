"""
core_models — canonical exports (v3)

This module re-exports the stable, ontology-aligned symbols so services
share a single vocabulary. Legacy constants are intentionally removed.
"""

# Canonical ontology (single source of truth)
from .ontology import (
    NodeType, EdgeType, Sensitivity,
    SCHEMA_ID_DECISION, SCHEMA_ID_EVENT, SCHEMA_ID_EDGE,
    ID_RE, DOMAIN_RE, ANCHOR_RE, EDGE_ID_RE, TIMESTAMP_Z_RE,
    make_anchor, parse_anchor, edge_id, utc_z,
)

__all__ = [
    "NodeType","EdgeType","Sensitivity",
    "SCHEMA_ID_DECISION","SCHEMA_ID_EVENT","SCHEMA_ID_EDGE",
    "ID_RE","DOMAIN_RE","ANCHOR_RE","EDGE_ID_RE","TIMESTAMP_Z_RE",
    "make_anchor","parse_anchor","edge_id","utc_z",
]