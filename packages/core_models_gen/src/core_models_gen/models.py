"""TEMPORARY RUNTIME SHIMS.
These permissive Pydantic v2 envelopes keep Gateway stable while we complete JSON-schema codegen.
Replace with generated classes when `scripts/codegen_schemas.sh` covers all schemas.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class WhyDecisionAnchor(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

class GraphEdgesModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    edges: list[dict] = Field(default_factory=list)

class MemoryMetaModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    allowed_ids_fp: Optional[str] = None
    policy_fp:     Optional[str] = None
    snapshot_etag: Optional[str] = None

class AnswerOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    role: Optional[str] = None

class AnswerBlocks(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    lead: str
    description: Optional[str] = None
    key_events: Optional[list[str]] = None
    next: Optional[str] = None
    owner: Optional[AnswerOwner] = None
    decision_id: Optional[str] = None

class WhyDecisionAnswer(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    blocks: AnswerBlocks

class CompletenessFlags(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

class WhyDecisionEvidence(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    anchor: WhyDecisionAnchor | dict | None = None
    graph:  GraphEdgesModel   | dict | None = None
    meta:   MemoryMetaModel   | dict | None = None
    allowed_ids: list[str] | None = None
    snapshot_etag: Optional[str] = None
    events: list[dict] | None = None

class WhyDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    anchor: WhyDecisionAnchor
    graph:  GraphEdgesModel
    meta:   MemoryMetaModel
    answer: WhyDecisionAnswer
    completeness_flags: CompletenessFlags | None = None