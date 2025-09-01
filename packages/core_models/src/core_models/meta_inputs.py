from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ConfigDict


class MetaInputs(BaseModel):
    """JSON-first input schema for meta assembly.

    All telemetry fields required to build a canonical ``MetaInfo`` are
    explicitly enumerated here.  Extra keys are rejected via the
    ``extra="forbid"`` configuration to prevent accidental drift.
    Consumers should instantiate this model with untrusted inputs and
    then pass it to :func:`shared.meta_builder.build_meta` to obtain a
    validated :class:`MetaInfo` instance.
    """

    # Required identifiers and fingerprints
    policy_id: str
    prompt_id: str
    prompt_fingerprint: str  # may or may not include "sha256:" prefix
    bundle_fingerprint: str
    bundle_size_bytes: int
    # Token accounting
    prompt_tokens: int
    evidence_tokens: int
    max_tokens: int
    # Snapshot and versioning
    snapshot_etag: str
    gateway_version: str
    selector_model_id: Optional[str] = None
    # Fallback and retry behaviour
    fallback_used: bool
    fallback_reason: Optional[str] = None
    retries: int
    # Latency and validator metrics
    latency_ms: int
    validator_error_count: int
    # Selector/evidence metrics
    evidence_metrics: Dict[str, Any] = Field(default_factory=dict)
    # Trace correlation
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    # Compact validator signal for public payloads
    validator_warnings: list[str] = Field(default_factory=list)
    # Load shed indicator
    load_shed: bool = False
    # Event shaping telemetry
    events_total: Optional[int] = None
    events_truncated: Optional[bool] = None
    snapshot_available: Optional[bool] = None

    model_config = ConfigDict(extra="forbid")