from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


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
    events: List[Dict[str, Any]]
    transitions: WhyDecisionTransitions
    allowed_ids: List[str]
    supporting_ids: List[str]
    rationale_note: Optional[str] = None


class WhyDecisionAnswer(BaseModel):
    short_answer: str
    supporting_ids: List[str]
    rationale_note: Optional[str] = None


class CompletenessFlags(BaseModel):
    has_preceding: bool = False
    has_succeeding: bool = False
    event_count: int = 0


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