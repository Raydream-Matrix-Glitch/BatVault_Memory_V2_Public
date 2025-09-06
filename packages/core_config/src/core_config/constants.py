import os
import warnings
from typing import Optional


# Legacy byte caps (kept for backward-compat in logs only)
MAX_PROMPT_BYTES = int(os.getenv("MAX_PROMPT_BYTES", "8192"))
SELECTOR_TRUNCATION_THRESHOLD = int(os.getenv("SELECTOR_TRUNCATION_THRESHOLD", "6144"))

# HTTP retry/backoff controls
HTTP_RETRY_BASE_MS = int(os.getenv("HTTP_RETRY_BASE_MS", "50"))
HTTP_RETRY_JITTER_MS = int(os.getenv("HTTP_RETRY_JITTER_MS", "200"))

# -------- Token-aware budgets (new) -----------------------------------
# Total context window of the control model (e.g., 2048).
# If CONTROL_CONTEXT_WINDOW is unset, fall back to VLLM_MAX_MODEL_LEN for convenience.
CONTROL_CONTEXT_WINDOW = int((os.getenv("CONTROL_CONTEXT_WINDOW") or os.getenv("VLLM_MAX_MODEL_LEN") or "2048"))
# Desired completion budget; router will clamp to remaining room
CONTROL_COMPLETION_TOKENS = int(os.getenv("CONTROL_COMPLETION_TOKENS", "512"))
# Guard tokens for wrappers/stop sequences/system prompts
CONTROL_PROMPT_GUARD_TOKENS = int(os.getenv("CONTROL_PROMPT_GUARD_TOKENS", "32"))
LLM_MIN_COMPLETION_TOKENS = int(os.getenv("LLM_MIN_COMPLETION_TOKENS", "16"))
GATE_SAFETY_HEADROOM_TOKENS = int(os.getenv("GATE_SAFETY_HEADROOM_TOKENS", "128"))
# Unified short-answer character cap:
# Prefer SHORT_ANSWER_MAX_CHARS; fall back to legacy ANSWER_CHAR_CAP to keep older envs working.
# If neither is set, default to 320 to match historical behavior.
SHORT_ANSWER_MAX_CHARS = int(os.getenv("SHORT_ANSWER_MAX_CHARS") or os.getenv("ANSWER_CHAR_CAP", "320"))

# Maximum number of sentences permitted in short answers.
# Prefer new SHORT_ANSWER_MAX_SENTENCES; fall back to legacy ANSWER_SENTENCE_CAP; default 2.
try:
    _sent_env: Optional[str] = os.getenv("SHORT_ANSWER_MAX_SENTENCES") or os.getenv("ANSWER_SENTENCE_CAP") or "2"
    SHORT_ANSWER_MAX_SENTENCES = int(_sent_env)
except Exception:
    SHORT_ANSWER_MAX_SENTENCES = 2

# -------- Gate shrink knobs (deterministic) -------------------------------
GATE_COMPLETION_SHRINK_FACTOR = float(os.getenv("GATE_COMPLETION_SHRINK_FACTOR", "0.8"))
GATE_SHRINK_JITTER_PCT = float(os.getenv("GATE_SHRINK_JITTER_PCT", "0.15"))
GATE_MAX_SHRINK_RETRIES = int(os.getenv("GATE_MAX_SHRINK_RETRIES", "2"))


# Soft selector threshold to decide whether to try compaction at all (tokens)
SELECTOR_TRUNCATION_THRESHOLD_TOKENS = int(
    os.getenv("SELECTOR_TRUNCATION_THRESHOLD_TOKENS", str(max(256, CONTROL_CONTEXT_WINDOW // 2)))
)
MIN_EVIDENCE_ITEMS = int(os.getenv("MIN_EVIDENCE_ITEMS", "1"))
SELECTOR_MODEL_ID = os.getenv("SELECTOR_MODEL_ID", "selector_v1")

# Redis TTL constants (Milestone 2 – caching strategy §H)
_ttl_resolver_new = os.getenv("TTL_RESOLVER_CACHE_SEC")
_ttl_resolver_old = os.getenv("CACHE_TTL_RESOLVER_SEC")
if _ttl_resolver_new is not None:
    TTL_RESOLVER_CACHE_SEC = int(_ttl_resolver_new)
    # If both are set and differ, warn and prefer the new one
    if _ttl_resolver_old is not None and _ttl_resolver_old != _ttl_resolver_new:
        warnings.warn(
            "Both TTL_RESOLVER_CACHE_SEC and CACHE_TTL_RESOLVER_SEC are set; "
            "preferring TTL_RESOLVER_CACHE_SEC. Please remove CACHE_TTL_RESOLVER_SEC.",
            DeprecationWarning,
        )
elif _ttl_resolver_old is not None:
    # Back-compat: accept the old var if the new one isn't set
    TTL_RESOLVER_CACHE_SEC = int(_ttl_resolver_old)
else:
    TTL_RESOLVER_CACHE_SEC = 300  # default 5 min

# Back-compat alias so legacy imports still work during deprecation
CACHE_TTL_RESOLVER_SEC = TTL_RESOLVER_CACHE_SEC
TTL_EXPAND_CACHE_SEC   = int(os.getenv("TTL_EXPAND_CACHE_SEC",   "60"))    # 1 min
TTL_EVIDENCE_CACHE_SEC = int(os.getenv("TTL_EVIDENCE_CACHE_SEC", "900"))   # 15 min

# ── Gateway schema-mirror cache (Milestone-4 §I1) ────────────────────────
TTL_SCHEMA_CACHE_SEC = int(os.getenv("TTL_SCHEMA_CACHE_SEC", "600"))

# Model identifiers (override via ENV)
RESOLVER_MODEL_ID = os.getenv("RESOLVER_MODEL_ID", "bi_encoder_v1")


# Stage budgets (ms) – env override keeps tests happy
TIMEOUT_SEARCH_MS   = int(os.getenv("TIMEOUT_SEARCH_MS",  "800"))
TIMEOUT_EXPAND_MS   = int(os.getenv("TIMEOUT_EXPAND_MS", "250"))
TIMEOUT_ENRICH_MS   = int(os.getenv("TIMEOUT_ENRICH_MS","600"))
TIMEOUT_LLM_MS      = int(os.getenv("TIMEOUT_LLM_MS",   "1500"))
TIMEOUT_VALIDATE_MS = int(os.getenv("TIMEOUT_VALIDATE_MS","300"))

# Embedding dimension and alias for back-compat
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
SIM_DIM = EMBEDDING_DIM
HEALTH_PORT = int(os.getenv("BATVAULT_HEALTH_PORT", "8081"))

_STAGE_TIMEOUTS_MS = {
    "search": TIMEOUT_SEARCH_MS,
    "expand": TIMEOUT_EXPAND_MS,
    "enrich": TIMEOUT_ENRICH_MS,
    "llm": TIMEOUT_LLM_MS,
    "validate": TIMEOUT_VALIDATE_MS,
}

def timeout_for_stage(stage: str) -> float:
    return _STAGE_TIMEOUTS_MS.get(stage, TIMEOUT_LLM_MS) / 1000.0