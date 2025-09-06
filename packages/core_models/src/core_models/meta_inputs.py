from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

class RequestInfo(BaseModel):
    intent: str
    anchor_id: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    ts_utc: str

class AllowedIdsPolicy(BaseModel):
    mode: Literal["include_all", "cap_top_k"] = "include_all"
    cap_k: Optional[int] = None
    cap_basis: Optional[Literal["weight", "recency", "similarity"]] = None
    cap_reason: Optional[str] = None

class LLMConfig(BaseModel):
    mode: Literal["off", "auto", "force", "none"] = "off"
    model: Optional[str] = None

class Policy(BaseModel):
    policy_id: str
    prompt_id: str
    allowed_ids_policy: AllowedIdsPolicy = Field(default_factory=AllowedIdsPolicy)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    gateway_version: Optional[str] = None
    selector_policy_id: Optional[str] = None
    env: Dict[str, Any] = Field(default_factory=dict)

class Budgets(BaseModel):
    context_window: int
    desired_completion_tokens: int
    guard_tokens: int
    overhead_tokens: int

class Fingerprints(BaseModel):
    prompt_fp: str
    bundle_fp: str
    snapshot_etag: str

class CountBreakdown(BaseModel):
    anchor: int = 0
    events: int = 0
    transitions: int = 0
    total: int = 0

class EvidenceCounts(BaseModel):
    pool: CountBreakdown
    prompt_included: CountBreakdown
    payload_serialized: CountBreakdown

class PromptExcluded(BaseModel):
    id: str
    reason: Literal["token_budget", "low_weight", "policy_cap", "other"] = "token_budget"

class EvidenceSets(BaseModel):
    pool_ids: List[str] = Field(default_factory=list)
    prompt_included_ids: List[str] = Field(default_factory=list)
    prompt_excluded_ids: List[PromptExcluded] = Field(default_factory=list)
    payload_included_ids: List[str] = Field(default_factory=list)
    payload_excluded_ids: List[str] = Field(default_factory=list)
    payload_source: Literal["pool", "prompt"] = "pool"

class SelectionMetrics(BaseModel):
    ranking_policy: Optional[str] = None
    ranked_pool_ids: List[str] = Field(default_factory=list)
    ranked_prompt_ids: List[str] = Field(default_factory=list)
    scores: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

class TruncationPass(BaseModel):
    prompt_tokens: int
    max_prompt_tokens: Optional[int] = None
    action: Literal["render", "clip", "render_retry", "stop"] = "render"

class TruncationMetrics(BaseModel):
    passes: List[TruncationPass] = Field(default_factory=list)
    prompt_truncation: bool = False     

class Runtime(BaseModel):
    latency_ms_total: int = 0
    stage_latencies_ms: Dict[str, int] = Field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    retries: int = 0

class ValidatorReport(BaseModel):
    error_count: int = 0
    warnings: List[str] = Field(default_factory=list)

class MetaInputs(BaseModel):
    """JSON-first input schema for canonical MetaInfo assembly."""
    policy_trace: Dict[str, Any] = Field(default_factory=dict)
    request: RequestInfo
    policy: Policy
    budgets: Budgets
    fingerprints: Fingerprints
    evidence_counts: EvidenceCounts
    evidence_sets: EvidenceSets
    selection_metrics: SelectionMetrics
    truncation_metrics: TruncationMetrics
    runtime: Runtime
    validator: ValidatorReport
    load_shed: bool = False
    model_config = ConfigDict(extra="forbid")