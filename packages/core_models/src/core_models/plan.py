from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
from .ontology import TruncationAction

class PlanBudgets(BaseModel):
    max_edges: int = Field(..., ge=0)
    max_events: int = Field(..., ge=0)
    timeout_ms: int = Field(..., ge=0)
    model_config = ConfigDict(extra="forbid")

class GatewayPlan(BaseModel):
    selector_policy_id: str = Field(..., min_length=1)
    budgets: PlanBudgets
    truncation_action: TruncationAction
    model_config = ConfigDict(extra="forbid")