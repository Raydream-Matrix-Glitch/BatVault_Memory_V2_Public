from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class WhyDecisionAnchor(BaseModel):
    id: str
    title: Optional[str] = None
    rationale: Optional[str] = None
    timestamp: Optional[str] = None
    decision_maker: Optional[str] = None

class WhyDecisionTransitions(BaseModel):
    preceding: List[Dict] = Field(default_factory=list)
    succeeding: List[Dict] = Field(default_factory=list)

class WhyDecisionEvidence(BaseModel):
    anchor: WhyDecisionAnchor
    events: List[Dict] = Field(default_factory=list)
    transitions: WhyDecisionTransitions = Field(default_factory=WhyDecisionTransitions)
    allowed_ids: List[str] = Field(default_factory=list)

class WhyDecisionAnswer(BaseModel):
    short_answer: str
    supporting_ids: List[str]
    rationale_note: Optional[str] = None

class CompletenessFlags(BaseModel):
    has_preceding: bool = False
    has_succeeding: bool = False
    event_count: int = 0

class WhyDecisionResponse(BaseModel):
    intent: str = "why_decision"
    evidence: WhyDecisionEvidence
    answer: WhyDecisionAnswer
    completeness_flags: CompletenessFlags
    meta: Dict
