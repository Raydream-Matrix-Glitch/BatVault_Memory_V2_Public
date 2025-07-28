from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

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

    arango_vector_index_enabled: bool = Field(default=True, alias="ARANGO_VECTOR_INDEX_ENABLED")
    embedding_dim: int = Field(default=384, alias="EMBEDDING_DIM")
    vector_metric: str = Field(default="cosine", alias="VECTOR_METRIC")
    vector_engine: str = Field(default="hnsw", alias="VECTOR_ENGINE")
    hnsw_m: int = Field(default=16, alias="HNSW_M")
    hnsw_efconstruction: int = Field(default=200, alias="HNSW_EFCONSTRUCTION")

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

    # LLM / embeddings
    llm_mode: str = Field(default="off", alias="LLM_MODE")
    enable_embeddings: bool = Field(default=False, alias="ENABLE_EMBEDDINGS")

def get_settings() -> "Settings":
    return Settings()  # type: ignore
