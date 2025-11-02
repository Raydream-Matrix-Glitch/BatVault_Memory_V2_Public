from __future__ import annotations
from typing import Literal, Tuple, Optional, Union
import re
from datetime import datetime, timezone
from dateutil import parser as _dtparser

# ── Enums (uppercase per v2 ontology) ─────────────────────────────────────────
NodeType = Literal["EVENT", "DECISION"]
EdgeType = Literal["LED_TO", "CAUSAL", "ALIAS_OF"]
Sensitivity = Literal["low", "medium", "high"]

# Canonical edge-type groupings for services to import (avoid string drift)
CAUSAL_EDGE_TYPES: Tuple[EdgeType, ...] = ("LED_TO", "CAUSAL")
ALIAS_EDGE_TYPES:  Tuple[EdgeType, ...] = ("ALIAS_OF",)
EDGE_TYPES:        Tuple[EdgeType, ...] = CAUSAL_EDGE_TYPES + ALIAS_EDGE_TYPES

# Non-graph enums used by Gateway/LLM budgeting (kept here to avoid drift)
TruncationAction = Literal["render","clip","render_retry","stop"]

def assert_truncation_action(val: str) -> str:
    v = str(val or "").strip().lower()
    allowed = {"render","clip","render_retry","stop"}
    if v not in allowed:
        raise ValueError(f"invalid TruncationAction: {val!r}")
    return v

# ── Schema $id constants (JSON-first) ─────────────────────────────────────────
SCHEMA_ID_DECISION = "https://schemas.batvault.dev/decision.schema.json"
SCHEMA_ID_EVENT    = "https://schemas.batvault.dev/event.schema.json"
SCHEMA_ID_EDGE     = "https://schemas.batvault.dev/edge.schema.json"

# ── Canonical regexes ────────────────────────────────────────────────────────
ID_RE          = re.compile(r"^[a-z0-9][a-z0-9\-:_\.]{2,}$")
DOMAIN_RE      = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")
ANCHOR_RE      = re.compile(r"^(?P<domain>[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*)#(?P<id>[a-z0-9][a-z0-9\-:_\.]{2,})$")
EDGE_ID_RE     = re.compile(r"^(ledto|causal|alias):(?P<from>.+?):(?P<to>.+)$")
TIMESTAMP_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

def is_valid_anchor(anchor: str) -> bool:
    """Return True if anchor matches ANCHOR_RE (canonical)."""
    return bool(ANCHOR_RE.match(anchor or ""))

# ── Helpers ───────────────────────────────────────────────────────────────────
def make_anchor(domain: str, node_id: str) -> str:
    if not DOMAIN_RE.match(str(domain or "")):
        raise ValueError(f"invalid domain: {domain}")
    if not ID_RE.match(str(node_id or "")):
        raise ValueError(f"invalid id: {node_id}")
    return f"{domain}#{node_id}"

def parse_anchor(anchor: str) -> Tuple[str, str]:
    m = ANCHOR_RE.match(anchor or "")
    if not m:
        raise ValueError(f"invalid anchor: {anchor}")
    return m.group('domain'), m.group('id')

def canonical_edge_type(kind: str) -> EdgeType:
    """
    Normalize various inputs to the canonical edge.type token.
    Accepts: 'LED_TO'|'ledto'|'led_to', 'CAUSAL', and 'ALIAS_OF'|'alias'|'alias_of'.
    """
    k = re.sub(r"[^a-z]", "", str(kind or "").lower())
    if k in {"ledto"}:
        return "LED_TO"
    if k in {"causal"}:
        return "CAUSAL"
    if k in {"alias", "aliasof"}:
        return "ALIAS_OF"
    raise ValueError(f"invalid edge kind: {kind}")

def edge_id(kind: str, from_anchor: str, to_anchor: str) -> str:
    # Canonicalize the public token, but keep the existing 'alias:' prefix on IDs.
    etype = canonical_edge_type(kind)
    k = {"LED_TO": "ledto", "CAUSAL": "causal", "ALIAS_OF": "alias"}[etype]
    if not ANCHOR_RE.match(from_anchor or ""):
        raise ValueError(f"invalid from anchor: {from_anchor}")
    if not ANCHOR_RE.match(to_anchor or ""):
        raise ValueError(f"invalid to anchor: {to_anchor}")
    return f"{k}:{from_anchor}:{to_anchor}"

def utc_z(ts: Optional[Union[str, datetime]]) -> str:
    """Validate or format to RFC3339 UTC 'Z' (YYYY-MM-DDTHH:MM:SSZ).

    Baseline v3: read-time code must not parse/normalize free-form timestamps.
    Strings must already be canonical; datetimes are coerced to UTC and formatted.
    """
    if ts is None:
        raise TypeError("timestamp is required")
    if isinstance(ts, str):
        if not TIMESTAMP_Z_RE.match(ts):
            raise ValueError("timestamp must match YYYY-MM-DDTHH:MM:SSZ (UTC)")
        return ts
    if isinstance(ts, datetime):
        dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    raise TypeError("timestamp must be str or datetime")

__all__ = [
    "NodeType","EdgeType","Sensitivity","TruncationAction","assert_truncation_action",
    "SCHEMA_ID_DECISION","SCHEMA_ID_EVENT","SCHEMA_ID_EDGE",
    "ID_RE","DOMAIN_RE","ANCHOR_RE","EDGE_ID_RE","TIMESTAMP_Z_RE",
    "make_anchor","parse_anchor","canonical_edge_type","edge_id","utc_z","is_valid_anchor",
    "CAUSAL_EDGE_TYPES","ALIAS_EDGE_TYPES","EDGE_TYPES",
]