from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
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
    
    @property
    def embedding_dimension(self) -> int:   # noqa: N802
        """Alias kept for legacy code paths that used
        `settings.embedding_dimension`."""
        return self.embedding_dim
    
    arango_vector_index_enabled: bool = Field(default=True, alias="ARANGO_VECTOR_INDEX_ENABLED")
    embedding_dim: int = Field(default=768, alias="EMBEDDING_DIM")
    vector_metric: str = Field(default="cosine", alias="VECTOR_METRIC")
    faiss_nlists: int = Field(default=100, alias="FAISS_NLISTS")
    # ---------------- Milestone‑3 additions ----------------
    # Evidence bundle cache TTL (15 min default per spec §H3)
    cache_ttl_evidence_sec: int = Field(default=900, alias="CACHE_TTL_EVIDENCE")
    # Prompt & selector sizing (spec §M4)
    max_prompt_bytes: int = Field(default=8192, alias="MAX_PROMPT_BYTES")
    selector_truncation_threshold: int = Field(default=6144, alias="SELECTOR_TRUNCATION_THRESHOLD")
    min_evidence_items: int = Field(default=1, alias="MIN_EVIDENCE_ITEMS")
    enable_selector_model: bool = Field(default=False, alias="ENABLE_SELECTOR_MODEL")

    # Graph/catalog names
    arango_graph_name: str = Field(default="batvault_graph", alias="ARANGO_GRAPH_NAME")
    arango_catalog_collection: str = Field(default="catalog", alias="ARANGO_CATALOG_COLLECTION")
    arango_meta_collection: str = Field(default="meta", alias="ARANGO_META_COLLECTION")

    # Redis
    cache_ttl_expand_sec: int = Field(default=60, alias="CACHE_TTL_EXPAND")   # spec §H3
    cache_ttl_resolve_sec: int = Field(default=300, alias="CACHE_TTL_RESOLVER")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # MinIO
    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="batvault-artifacts", alias="MINIO_BUCKET")
    minio_region: str = Field(default="us-east-1", alias="MINIO_REGION")
    minio_retention_days: int = Field(default=14, alias="MINIO_RETENTION_DAYS")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    # non-blocking MinIO uploads (§Tech-Spec A, “performance budgets”)
    minio_async_timeout: int = Field(default=3, alias="MINIO_ASYNC_TIMEOUT")
    memory_api_url: str = Field(
        default="http://memory_api:8000", alias="MEMORY_API_URL"
    )

    # LLM / embeddings
    llm_mode: str = Field(default="off", alias="LLM_MODE")
    enable_embeddings: bool = Field(default=False, alias="ENABLE_EMBEDDINGS")

    # ── API-edge rate-limiting (A-1) ─────────────────────────────────
    api_rate_limit_default: str = Field(
        default="100/minute", alias="API_RATE_LIMIT_DEFAULT"
    )

    # ── Stage time-outs (A-2) – milliseconds ───────────────────────
    timeout_search_ms: int = Field(default=TIMEOUT_SEARCH_MS,  alias="TIMEOUT_SEARCH_MS")
    timeout_expand_ms: int = Field(default=TIMEOUT_EXPAND_MS,  alias="TIMEOUT_EXPAND_MS")
    timeout_enrich_ms: int = Field(default=TIMEOUT_ENRICH_MS,  alias="TIMEOUT_ENRICH_MS")

def get_settings() -> "Settings":
    return Settings()  # type: ignore
