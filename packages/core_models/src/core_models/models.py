from pydantic import BaseModel, Field, field_validator, ConfigDict, model_validator
from core_utils import slugify_tag
from typing import Any, Dict, List, Optional, Literal
import re


class WhyDecisionAnchor(BaseModel):
    id: str
    title: Optional[str] = None
    # Legacy alias used by Memory API; allowed by the validator.
    # Keeping it here avoids extra='forbid' rejections during validation.
    option: Optional[str] = None
    rationale: Optional[str] = None
    timestamp: Optional[str] = None
    decision_maker: Optional[str] = None
    # Arrays default to [], not null, to avoid surprising "null" in responses.
    tags: List[str] = Field(default_factory=list)
    supported_by: List[str] = Field(default_factory=list)
    based_on: List[str] = Field(default_factory=list)
    # Memory API may include a flat list of neighbour transition *IDs* on the anchor.
    # Accept both strings and dicts; normalise strings → {"id": "<str>"} for schema hygiene.
    transitions: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("tags", "supported_by", "based_on", mode="before")
    @classmethod
    def _coerce_optional_lists(cls, v):
        # Preserve strictness but ensure None becomes []
        return [] if v is None else v

    @field_validator("transitions", mode="before")
    @classmethod
    def _coerce_transitions(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            out = []
            for item in v:
                if isinstance(item, str):
                    out.append({"id": item})
                elif isinstance(item, dict):
                    out.append(item)
            return out
        return []

    @model_validator(mode="after")
    def _mirror_option_to_title(self):
        # Safety net on the Gateway side if upstream normalisation was bypassed
        if not self.title and self.option:
            object.__setattr__(self, "title", self.option)
        return self
    model_config = ConfigDict(extra='forbid')

class MetaInfo(BaseModel):
    """
    Canonical, JSON-first meta block (single source of truth).
    Exactly one occurrence per field; validated before serialization.
    """
    policy_id: str
    prompt_id: str
    prompt_fingerprint: str  # "sha256:<hex>"
    bundle_fingerprint: str
    bundle_size_bytes: int
    prompt_tokens: int
    evidence_tokens: int
    max_tokens: int
    snapshot_etag: str
    fallback_used: bool
    fallback_reason: Optional[str] = None  # e.g., "llm_off", "parse_error", "style_violation"
    retries: int
    gateway_version: str
    selector_model_id: Optional[str] = None
    latency_ms: int
    validator_error_count: int
    evidence_metrics: Dict[str, Any] = Field(default_factory=dict)  # {step, selector_truncation, total_neighbors_found, final_evidence_count, dropped_evidence_ids}
    load_shed: bool = False
    # Event result shaping
    events_total: Optional[int] = None
    events_truncated: Optional[bool] = None
    snapshot_available: Optional[bool] = None

    # Path taken by the resolver when hydrating the anchor.  This field is
    # populated by the Gateway app for diagnostic purposes and may be
    # omitted from responses when not applicable.  It is defined on the
    # canonical MetaInfo model to permit dynamic assignment without
    # violating the "extra=forbid" constraint.
    resolver_path: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class WhyDecisionTransitions(BaseModel):
    """
    Representation of a decision's neighbouring transitions.  The fields
    ``preceding`` and ``succeeding`` are optional and only serialized
    when non-empty.  When a list is empty it is set to ``None`` by the
    Gateway builder and omitted from JSON output
    """
    preceding: Optional[List[Dict[str, Any]]] = None
    succeeding: Optional[List[Dict[str, Any]]] = None

    # Exclude ``None`` fields from the serialized representation.  This ensures
    # that absent transition lists do not appear as ``null`` in API responses.
    model_config = ConfigDict(extra='forbid', exclude_none=True)


class WhyDecisionEvidence(BaseModel):
    anchor: WhyDecisionAnchor
    events: List[Dict[str, Any]] = Field(default_factory=list)
    transitions: WhyDecisionTransitions = Field(default_factory=WhyDecisionTransitions)
    allowed_ids: List[str] = Field(default_factory=list)

    snapshot_etag: Optional[str] = Field(
        default=None,
        exclude=True,
        description=(
            "Corpus snapshot identifier returned by Memory-API. "
            "Used exclusively for cache-key generation and freshness checks."
        ),
    )
    # Forbid unknown fields on the public response envelope
    model_config = ConfigDict(extra='forbid')

class WhyDecisionAnswer(BaseModel):
    short_answer: str
    supporting_ids: List[str]
    model_config = ConfigDict(extra='forbid')

class CompletenessFlags(BaseModel):
    has_preceding: bool = False
    has_succeeding: bool = False
    event_count: int = 0
    model_config = ConfigDict(extra='forbid')

# --------------------------------------------------------------------------- #
#  EventModel – minimal milestone-3 schema (spec §S1/S3, tag rules spec §S3)  #
# --------------------------------------------------------------------------- #

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$')


class EventModel(BaseModel):
    """Standalone Event schema used by unit-tests."""

    id: str
    summary: str
    timestamp: str
    snippet: Optional[str] = None          # ≤120 chars (spec §S3)
    tags: List[str] = Field(default_factory=list)

    # ─── validators ────────────────────────────────────────────────────────
    @field_validator('id')
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError('id must match slug regex')
        return v

    @field_validator('snippet')
    @classmethod
    def _check_snippet(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 120:
            raise ValueError('snippet must be ≤ 120 characters')
        return v

    @field_validator('tags', mode='before')
    @classmethod
    def _slug_tags(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        out, seen = [], set()
        for raw in v:
            s = slugify_tag(str(raw))
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        return out

class WhyDecisionResponse(BaseModel):
    intent: str
    evidence: WhyDecisionEvidence
    answer: WhyDecisionAnswer
    completeness_flags: CompletenessFlags
    meta: MetaInfo
    bundle_url: Optional[str] = None
    model_config = ConfigDict(extra='forbid')

class PromptEnvelope(BaseModel):
    """
    JSON payload sent to the LLM: includes metadata, input question, evidence bundle,
    allowed IDs, and any output constraints.
    """
    prompt_version: str
    intent: str
    question: str
    evidence: Dict[str, Any]
    allowed_ids: List[str]
    constraints: Dict[str, Any]
    model_config = ConfigDict(extra='forbid')

class GatePlan(BaseModel):
    messages: List[Dict[str, str]]
    max_tokens: int
    prompt_tokens: int
    overhead_tokens: int
    evidence_tokens: int
    desired_completion_tokens: int
    shrinks: List[int] = Field(default_factory=list)
    fingerprints: Dict[str, str] | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')