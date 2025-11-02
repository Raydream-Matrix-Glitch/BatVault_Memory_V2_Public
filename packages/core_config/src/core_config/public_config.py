from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional

class PublicSigning(BaseModel):
    alg: Literal["Ed25519"] = "Ed25519"
    public_key_b64: Optional[str] = Field(default=None)

class PublicEndpoints(BaseModel):
    query: str = "/v2/query"
    bundles: str = "/v3/bundles"
    orient: str = "/v3/orient"

class PublicTimeouts(BaseModel):
    search: int
    expand: int
    enrich: int
    validate: int

class PublicConfig(BaseModel):
    gateway_base: str
    memory_base: str
    endpoints: PublicEndpoints
    timeouts_ms: PublicTimeouts
    signing: PublicSigning