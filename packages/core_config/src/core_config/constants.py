import os

MAX_PROMPT_BYTES = int(os.getenv("MAX_PROMPT_BYTES", "8192"))
SELECTOR_TRUNCATION_THRESHOLD = int(os.getenv("SELECTOR_TRUNCATION_THRESHOLD", "6144"))
MIN_EVIDENCE_ITEMS = int(os.getenv("MIN_EVIDENCE_ITEMS", "1"))
SELECTOR_MODEL_ID = "selector_v1"
SIM_DIM = int(os.getenv("EMBEDDING_DIM", "768"))  # vector index dimension                         # vector index dimension

# Redis TTL constants (Milestone 2 – caching strategy §H)
TTL_RESOLVER_CACHE_SEC = int(os.getenv("TTL_RESOLVER_CACHE_SEC", "300"))   # 5 min
TTL_EXPAND_CACHE_SEC   = int(os.getenv("TTL_EXPAND_CACHE_SEC",   "60"))    # 1 min
TTL_EVIDENCE_CACHE_SEC = int(os.getenv("TTL_EVIDENCE_CACHE_SEC", "900"))   # 15 min

# ── Gateway schema-mirror cache (Milestone-4 §I1) ────────────────────────
# Cache the upstream Field/Relation catalog for 10 min by default
TTL_SCHEMA_CACHE_SEC = int(os.getenv("TTL_SCHEMA_CACHE_SEC", "600"))

# ---------------------------------------------------------------------------
#  Model identifiers (override via ENV; spec §Milestone-2/3)
# ---------------------------------------------------------------------------
SELECTOR_MODEL_ID = os.getenv("SELECTOR_MODEL_ID", "selector_v1")
RESOLVER_MODEL_ID = os.getenv("RESOLVER_MODEL_ID", "bi_encoder_v1")


# Stage budgets (ms) – env override keeps tests happy
TIMEOUT_SEARCH_MS   = int(os.getenv("TIMEOUT_SEARCH_MS",  "800"))
TIMEOUT_EXPAND_MS   = int(os.getenv("TIMEOUT_EXPAND_MS", "250"))
TIMEOUT_ENRICH_MS   = int(os.getenv("TIMEOUT_ENRICH_MS","600"))
TIMEOUT_LLM_MS      = int(os.getenv("TIMEOUT_LLM_MS",   "1500"))
TIMEOUT_VALIDATE_MS = int(os.getenv("TIMEOUT_VALIDATE_MS","300"))

# ───── Gateway-specific helpers ─────────────────────────────────────────
# Resolver cache
CACHE_TTL_RESOLVER_SEC = int(os.getenv("CACHE_TTL_RESOLVER_SEC", "300"))
# Embedding dimension (vector index)
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
# Health endpoint port
HEALTH_PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8081"))

_STAGE_TIMEOUTS_MS = {
    "search": TIMEOUT_SEARCH_MS,
    "expand": TIMEOUT_EXPAND_MS,
    "enrich": TIMEOUT_ENRICH_MS,
    "llm": TIMEOUT_LLM_MS,
    "validate": TIMEOUT_VALIDATE_MS,
}

def timeout_for_stage(stage: str) -> float:
    return _STAGE_TIMEOUTS_MS.get(stage, TIMEOUT_LLM_MS)/1000.0