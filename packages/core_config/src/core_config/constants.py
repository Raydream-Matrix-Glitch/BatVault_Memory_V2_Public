import os

MAX_PROMPT_BYTES = int(os.getenv("MAX_PROMPT_BYTES", "8192"))
SELECTOR_TRUNCATION_THRESHOLD = int(os.getenv("SELECTOR_TRUNCATION_THRESHOLD", "6144"))
MIN_EVIDENCE_ITEMS = int(os.getenv("MIN_EVIDENCE_ITEMS", "1"))
SELECTOR_MODEL_ID = "selector_v1"
SIM_DIM = int(os.getenv("EMBEDDING_DIM", "768"))  # vector index dimension                         # vector index dimension

# ---------------------------------------------------------------------------
# Model-identifier constants
# ---------------------------------------------------------------------------
# Logged in `meta.model_metrics` for every request.  Defaults follow the
# canonical names in the spec but can be overridden via env-vars.
#
# Keeping them in *core_config* prevents circular imports and lets any service
# (gateway, metrics, etc.) share the same source of truth.
SELECTOR_MODEL_ID = os.getenv("SELECTOR_MODEL_ID", "selector_v1")
RESOLVER_MODEL_ID = os.getenv("RESOLVER_MODEL_ID", "bi_encoder_v1")

# Stage budgets (ms)
TIMEOUT_SEARCH_MS   = 800
TIMEOUT_EXPAND_MS   = 250
TIMEOUT_ENRICH_MS   = 600
TIMEOUT_LLM_MS      = 1500
TIMEOUT_VALIDATE_MS = 300

_STAGE_TIMEOUTS_MS = {
    "search": TIMEOUT_SEARCH_MS,
    "expand": TIMEOUT_EXPAND_MS,
    "enrich": TIMEOUT_ENRICH_MS,
    "llm": TIMEOUT_LLM_MS,
    "validate": TIMEOUT_VALIDATE_MS,
}

def timeout_for_stage(stage: str) -> float:
    return _STAGE_TIMEOUTS_MS.get(stage, TIMEOUT_LLM_MS)/1000.0