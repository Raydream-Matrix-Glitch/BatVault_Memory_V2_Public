from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, List, Optional
import re


class WhyDecisionAnchor(BaseModel):
    id: str
    title: Optional[str] = None
    rationale: Optional[str] = None
    timestamp: Optional[str] = None
    decision_maker: Optional[str] = None


class WhyDecisionTransitions(BaseModel):
    preceding: List[Dict[str, Any]] = Field(default_factory=list)
    succeeding: List[Dict[str, Any]] = Field(default_factory=list)


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

class WhyDecisionAnswer(BaseModel):
    short_answer: str
    supporting_ids: List[str]
    rationale_note: Optional[str] = None


class CompletenessFlags(BaseModel):
    has_preceding: bool = False
    has_succeeding: bool = False
    event_count: int = 0

# --------------------------------------------------------------------------- #
#  EventModel – minimal milestone-3 schema (spec §S1/S3, tag rules spec §S3)  #
# --------------------------------------------------------------------------- #

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$')


def _slugify(tag: str) -> str:
    """lower-case, collapse non-alphanumerics to ‘-’, trim dashes"""
    return re.sub(r'[^a-z0-9]+', '-', tag.lower()).strip('-')


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
            s = _slugify(str(raw))
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        return out

class WhyDecisionResponse(BaseModel):
    intent: str
    evidence: WhyDecisionEvidence
    answer: WhyDecisionAnswer
    completeness_flags: CompletenessFlags
    meta: Dict[str, Any]

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