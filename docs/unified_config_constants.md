# V2 Batvault Memory - Unified Configuration Constants

## Performance & Size Budgets

```python
# Evidence size management
MAX_PROMPT_BYTES = 8192  # ~4k tokens with metadata
SELECTOR_TRUNCATION_THRESHOLD = 6144  # Start truncating before hard limit
MIN_EVIDENCE_ITEMS = 1  # Always keep at least anchor + 1 supporting item

# Vector dimensions
SIM_DIM = 768  # Vector dimension for HNSW index

# Performance budgets (milliseconds)
TTFB_SLUG_MS = 600      # Known slug lookup
TTFB_SEARCH_MS = 2500   # Text search
P95_ASK_MS = 3000       # /v2/ask total
P95_QUERY_MS = 4500     # /v2/query total (updated for NL routing)

# Stage timeouts
TIMEOUT_SEARCH_MS = 800
TIMEOUT_GRAPH_EXPAND_MS = 250
TIMEOUT_ENRICH_MS = 600
TIMEOUT_LLM_MS = 1500
TIMEOUT_VALIDATOR_MS = 300
```

## Cache TTLs

```python
# Cache policies (seconds)
CACHE_TTL_RESOLVER = 300      # 5min - normalized decision_ref
CACHE_TTL_EVIDENCE = 900      # 15min - evidence bundles
CACHE_TTL_LLM_JSON = 120      # 2min - hot anchors
CACHE_TTL_EXPAND = 60         # 1min - graph expansion results
```

## Intent Quotas (Standardized)

| Intent | k-limit | Events | Preceding | Succeeding | Notes |
|--------|---------|--------|-----------|------------|-------|
| `why_decision` | 1 | unbounded* | unbounded* | unbounded* | Core reasoning |
| `who_decided` | 1 | unbounded* | 0 | 0 | Decision makers only |
| `when_decided` | 1 | unbounded* | unbounded* | unbounded* | Timeline context |
| `chains` | unlimited | unbounded* | N/A | N/A | Full decision chains |

*Subject to `MAX_PROMPT_BYTES` truncation with `selector_truncation=true` logging

## Observability Fields (Complete Set)

```json
{
  "evidence_metrics": {
    "total_neighbors_found": 12,
    "selector_truncation": true,
    "final_evidence_count": 8,
    "dropped_evidence_ids": ["low-score-event-1"],
    "bundle_size_bytes": 7680,
    "max_prompt_bytes": 8192
  },
  "model_metrics": {
    "resolver_confidence": 0.85,
    "selector_model_id": "selector_v1",
    "resolver_model_id": "bi_encoder_v1",
    "reranker_model_id": "cross_encoder_v1"
  },
  "performance_metrics": {
    "stage_timings": {
      "resolve_ms": 120,
      "expand_ms": 80,
      "enrich_ms": 200,
      "bundle_ms": 50,
      "llm_ms": 800,
      "validate_ms": 30
    }
  }
}
```

## Weak AI Model Specifications

```python
# Resolver models
RESOLVER_BI_ENCODER = "sentence-transformers/all-MiniLM-L6-v2"  # 384d
RESOLVER_CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RESOLVER_BM25_FALLBACK = True  # Always available

# Selector model (evidence truncation)
SELECTOR_MODEL_TYPE = "gbdt"  # or "logistic" or "tiny_mlp"
SELECTOR_FEATURES = [
    "text_similarity",    # cosine(event_text, anchor_rationale)
    "graph_degree",       # in/out degree
    "recency_delta",      # days from anchor timestamp
    "tag_overlap",        # intersection(event.tags, anchor.tags)
    "historical_priors"   # future: click/feedback data
]

# Graph embeddings
GRAPH_EMBEDDING_METHOD = "node2vec"  # or "lightgcn"
GRAPH_EMBEDDING_DIM = 128
```

## Validation Constants

```python
# ID validation
ID_REGEX = r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$"

# Content length limits
MAX_RATIONALE_CHARS = 600
MAX_REASON_CHARS = 280
MAX_SUMMARY_CHARS = 120
MAX_SNIPPET_CHARS = 120
MAX_SHORT_ANSWER_CHARS = 320

# Retry policies
MAX_LLM_RETRIES = 2
MAX_HTTP_RETRIES = 1
RETRY_BACKOFF_MS = 300
```

## Feature Flags

```python
# ML model toggles
ENABLE_EMBEDDINGS = True          # Vector search vs BM25 only
ENABLE_SELECTOR_MODEL = True      # Learned vs deterministic selection
ENABLE_GRAPH_EMBEDDINGS = False   # Graph ML features

# Operational toggles
ENABLE_LOAD_SHEDDING = True       # Auto fallback under load
ENABLE_ARTIFACT_RETENTION = True # Store audit artifacts
ENABLE_CACHING = True            # Redis caching layer
```