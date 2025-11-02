from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Any, Optional
from core_config.constants import (
    TIMEOUT_SEARCH_MS,
    TIMEOUT_EXPAND_MS,
    TIMEOUT_ENRICH_MS,
)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="dev", alias="ENVIRONMENT")
    service_log_level: str = Field(default="INFO", alias="SERVICE_LOG_LEVEL")
    request_log_sample_rate: float = Field(default=1.0, alias="REQUEST_LOG_SAMPLE_RATE")

    # Auth
    auth_disabled: bool = Field(default=True, alias="AUTH_DISABLED")

    # Performance budgets
    perf_ask_p95_ms: int = Field(default=3000, alias="PERF_ASK_P95_MS")
    perf_query_p95_ms: int = Field(default=4500, alias="PERF_QUERY_P95_MS")

    # Arango
    arango_url: str = Field(default="http://arangodb:8529", alias="ARANGO_URL")
    arango_db: str = Field(default="batvault", alias="ARANGO_DB")
    arango_root_user: str = Field(default="root", alias="ARANGO_ROOT_USER")
    arango_root_password: str = Field(default="batvault", alias="ARANGO_ROOT_PASSWORD")
    # Convenience aliases so other layers can reference a generic
    # “username / password” without caring about the role name.
    @property
    def arango_username(self) -> str:  # noqa: D401
        """Return the configured root user (alias)."""
        return self.arango_root_user

    @property
    def arango_password(self) -> str:  # noqa: D401
        """Return the configured root password (alias)."""
        return self.arango_root_password
      
    arango_vector_index_enabled: bool = Field(default=True, alias="ARANGO_VECTOR_INDEX_ENABLED")
    embedding_dim: int = Field(default=768, alias="EMBEDDING_DIM")
    vector_metric: str = Field(default="cosine", alias="VECTOR_METRIC")
    faiss_nlists: int = Field(default=100, alias="FAISS_NLISTS")

    # Sensitivity ordering (most restrictive wins). Accepts comma string via env.
    sensitivity_order_raw: str = Field(default="low,medium,high", alias="SENSITIVITY_ORDER")
    @property
    def sensitivity_order(self) -> list[str]:  # noqa: D401
        """Ordering of sensitivity levels, least→most restrictive."""
        try:
            return [x.strip() for x in (self.sensitivity_order_raw or "").split(",") if x.strip()]
        except Exception:
            return ["low", "medium", "high"]

    # Prompt & selector sizing (spec §M4)
    min_evidence_items: int = Field(default=1, alias="MIN_EVIDENCE_ITEMS")
    enable_selector_model: bool = Field(default=False, alias="ENABLE_SELECTOR_MODEL")

    # Graph/catalog names
    arango_graph_name: str = Field(default="batvault_graph", alias="ARANGO_GRAPH_NAME")
    arango_catalog_collection: str = Field(default="catalog", alias="ARANGO_CATALOG_COLLECTION")
    arango_meta_collection: str = Field(default="meta", alias="ARANGO_META_COLLECTION")

    # Redis
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # MinIO
    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="batvault-artifacts", alias="MINIO_BUCKET")
    minio_region: str = Field(default="us-east-1", alias="MINIO_REGION")
    minio_retention_days: int = Field(default=14, alias="MINIO_RETENTION_DAYS")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    minio_public_endpoint: str | None = Field(default=None, alias="MINIO_PUBLIC_ENDPOINT")
    # non-blocking MinIO uploads (§Tech-Spec A, “performance budgets”)
    minio_async_timeout: int = Field(default=3, alias="MINIO_ASYNC_TIMEOUT")
    memory_api_url: str = Field(
        default="http://memory_api:8000", alias="MEMORY_API_URL"
    )
    # OPA (optional; externalize policy decisions)
    opa_url: Optional[str] = Field(default=None, alias="OPA_URL")
    opa_decision_path: str = Field(default="/v1/data/batvault/decision", alias="OPA_DECISION_PATH")
    opa_timeout_ms: int = Field(default=1000, alias="OPA_TIMEOUT_MS")
    # If provided, becomes the authoritative policy_fp when using OPA bundles.
    opa_bundle_sha: Optional[str] = Field(default=None, alias="OPA_BUNDLE_SHA")

    # LLM / embeddings
    llm_mode: str = Field(default="off", alias="LLM_MODE")
    # Endpoint-specific policy overrides
    ask_llm_mode: str = Field(default="off", alias="ASK_LLM_MODE")
    query_llm_mode: str = Field(default="auto", alias="QUERY_LLM_MODE")
    enable_embeddings: bool = Field(default=False, alias="ENABLE_EMBEDDINGS")

    # If true, Gateway attempts a semantic rerank of BM25 candidates.
    rerank_enable: bool = Field(default=False, alias="RERANK_ENABLE")
    # Margin threshold for “confident top-1” (see ai_retrieval.md).
    rerank_margin: float = Field(default=1e-6, alias="RERANK_MARGIN")
    # How many BM25 candidates to consider before optional rerank.
    resolver_top_k: int = Field(default=24, alias="RESOLVER_TOP_K")
    # Cross-encoder model id (HF hub or local path).
    cross_encoder_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2", alias="CROSS_ENCODER_MODEL")
    # Rerank only when candidate count is small (2..RERANK_PAIR_MAX).
    rerank_pair_max: int = Field(default=10, alias="RERANK_PAIR_MAX")
    # Hard budget for rerank scoring (milliseconds).
    rerank_timeout_ms: int = Field(default=50, alias="RERANK_TIMEOUT_MS")

    # Evidence heuristics
    enable_day_summary_dedup: bool = Field(default=False, alias="ENABLE_DAY_SUMMARY_DEDUP")
    # Answer shaping
    because_event_count: int = Field(default=3, alias="BECAUSE_EVENT_COUNT")

    # Canonical answer budgets (prefer these; legacy kept for back-compat)
    short_answer_max_chars: int = Field(default=320, alias="SHORT_ANSWER_MAX_CHARS")
    short_answer_max_sentences: int = Field(default=2, alias="SHORT_ANSWER_MAX_SENTENCES")

    # ── API-edge rate-limiting (A-1) ─────────────────────────────────
    api_rate_limit_default: str = Field(
        default="100/minute", alias="API_RATE_LIMIT_DEFAULT"
    )

    # ── Stage time-outs (A-2) – milliseconds ───────────────────────
    timeout_search_ms: int = Field(default=TIMEOUT_SEARCH_MS,  alias="TIMEOUT_SEARCH_MS")
    timeout_expand_ms: int = Field(default=TIMEOUT_EXPAND_MS,  alias="TIMEOUT_EXPAND_MS")
    timeout_enrich_ms: int = Field(default=TIMEOUT_ENRICH_MS,  alias="TIMEOUT_ENRICH_MS")
    # LLM routing
    # Primary endpoint (preferred).
    llm_endpoint: str | None = Field(default=None, alias="LLM_ENDPOINT")
    control_model_endpoint: str = Field(default="http://vllm-control:8010", alias="CONTROL_MODEL_ENDPOINT")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=512, alias="LLM_MAX_TOKENS")
    vllm_gpu_util: str | None = Field(default=None, alias="VLLM_GPU_UTIL")
    vllm_max_model_len: str | None = Field(default=None, alias="VLLM_MAX_MODEL_LEN")
    vllm_max_num_seqs: str | None = Field(default=None, alias="VLLM_MAX_NUM_SEQS")
    vllm_max_batched_tokens: str | None = Field(default=None, alias="VLLM_MAX_BATCHED_TOKENS")
    # HTTP client pool
    http_max_keepalive: int = Field(default=20, alias="HTTP_MAX_KEEPALIVE")
    http_max_connections: int = Field(default=100, alias="HTTP_MAX_CONNECTIONS")
    http_keepalive_expiry: float = Field(default=30.0, alias="HTTP_KEEPALIVE_EXPIRY")
    http_connect_timeout: float = Field(default=5.0, alias="HTTP_CONNECT_TIMEOUT")
    http_read_timeout: float = Field(default=10.0, alias="HTTP_READ_TIMEOUT")
    http_write_timeout: float = Field(default=10.0, alias="HTTP_WRITE_TIMEOUT")
    http_pool_timeout: float = Field(default=5.0, alias="HTTP_POOL_TIMEOUT")

    # Misc feature flags / compatibility
    cite_all_ids: bool = Field(default=False, alias="CITE_ALL_IDS")
    openai_disabled: bool = Field(default=False, alias="OPENAI_DISABLED")

    # Load-shed / Redis budgets
    redis_get_budget_ms: int = Field(default=100, alias="REDIS_GET_BUDGET_MS")

    # Policy registry
    policy_registry_path: str | None = Field(default=None, alias="POLICY_REGISTRY_PATH")
    policy_registry_url: str | None = Field(default=None, alias="POLICY_REGISTRY_URL")

    # LLM metadata / adapters
    vllm_model_name: str | None = Field(default=None, alias="VLLM_MODEL_NAME")
    cross_encoder_model: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2", alias="CROSS_ENCODER_MODEL")

    # --- Back-compat shim (temporary) ---
    # If LLM_ENDPOINT is not provided, fall back to CONTROL_MODEL_ENDPOINT.
    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        try:
            if not self.llm_endpoint:
                object.__setattr__(self, "llm_endpoint", self.control_model_endpoint)
        except Exception:
            # Best-effort; leave defaults as-is on failure.
            pass

def get_settings() -> "Settings":
    return Settings()  # type: ignore
