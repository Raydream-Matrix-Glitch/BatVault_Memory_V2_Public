from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal, Dict

class Anchor(BaseModel):
    id: str = Field(..., description="Anchor node id")
    title: Optional[str] = None

class SupportingEvidence(BaseModel):
    id: str
    kind: Literal["node","edge","transition"] = "node"
    weight: float | None = None

class WhyDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = "1"
    request_id: str
    intent: Literal["why_decision"] = "why_decision"
    policy_id: str
    prompt_id: str
    prompt_fingerprint: str

    anchor: Anchor
    supporting_ids: List[str] = Field(default_factory=list)
    allowed_ids: List[str] = Field(default_factory=list)

    short_answer: str = ""
    fallback_used: bool = False
    snapshot_etag: Optional[str] = None

    # Opaque dump for trace/audit links (s3/minio refs, timings, etc.)
    artifacts: Dict[str, object] = Field(default_factory=dict)

    def validate_semantics(self) -> None:
        """Cheap semantic checks: supporting ⊆ allowed and anchor is cited."""
        if not set(self.supporting_ids).issubset(set(self.allowed_ids)):
            raise ValueError("supporting_ids must be a subset of allowed_ids")
        if self.anchor.id not in self.supporting_ids:
            raise ValueError("anchor.id must be present in supporting_ids")
