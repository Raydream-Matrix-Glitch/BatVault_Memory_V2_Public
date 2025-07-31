# V2 Batvault Memory - Core Specification (Updated)

## 1. System Overview

**Vision**: Intent-first memory system with natural language and structured endpoints

- **Structured**: `POST /v2/ask` with explicit intent + options
- **Natural Language**: `POST /v2/query` with LLM function routing
- **Evidence-based**: k=1 graph expansion with deterministic quotas
- **Auditable**: Complete artifact retention with fingerprints
- **Schema-agnostic**: Field catalog enables zero-code field additions
- **Orphan-tolerant**: Events without decisions, decisions without predecessors/successors are valid

**Request Flow**: `query → resolve anchor → expand graph → build evidence → prompt LLM → validate → stream`

## 2. Core Components

| Component | Responsibilities |
|-----------|------------------|
| **API Edge** | HTTP, auth, rate-limit, idempotency |
| **Gateway** | Orchestration, evidence bundling, LLM calls, validation |
| **Intent Router** | Natural language → function routing |
| **Memory API** | Graph operations, enrichment, schema catalog |
| **Ingest** | JSON validation, normalization, ArangoDB persistence |

## 3. Technology Stack

- **Backend**: Python 3.11, FastAPI
- **Frontend**: Node 20, Next.js/React
- **Storage**: ArangoDB Community (graph+vector)
- **Cache**: Redis 7
- **Artifacts**: MinIO/S3

## 4. Configuration Constants

### 4.1. Performance & Size Budgets
```python
# Evidence size management
MAX_PROMPT_BYTES = 8192            # Hard limit for evidence bundle size
SELECTOR_TRUNCATION_THRESHOLD = 6144  # Start truncating before hard limit
MIN_EVIDENCE_ITEMS = 1             # Always keep anchor + 1 supporting item
SIM_DIM = 768                      # Vector dimension for HNSW index

# Performance budgets (milliseconds)
TTFB_SLUG_MS = 600                 # Known slug lookup
TTFB_SEARCH_MS = 2500              # Text search
P95_ASK_MS = 3000                  # /v2/ask total
P95_QUERY_MS = 4500                # /v2/query total

# Stage timeouts
TIMEOUT_SEARCH_MS = 800
TIMEOUT_GRAPH_EXPAND_MS = 250
TIMEOUT_ENRICH_MS = 600
TIMEOUT_LLM_MS = 1500
TIMEOUT_VALIDATOR_MS = 300
```

### 4.2. Cache TTLs
```python
# Cache policies (seconds)
CACHE_TTL_RESOLVER = 300           # 5min - normalized decision_ref
CACHE_TTL_EVIDENCE = 900           # 15min - evidence bundles
CACHE_TTL_LLM_JSON = 120           # 2min - hot anchors
CACHE_TTL_EXPAND = 60              # 1min - graph expansion results
```

## 5. API Contracts

### 5.1. Structured Endpoint

```http
POST /v2/ask
{
  "intent": "why_decision|who_decided|when_decided|chains",
  "decision_ref": "panasonic-exit-plasma-2012",
  "options": {"llm_mode": "auto|off|force", "timeout_ms": 3500}
}
```

### 5.2. Natural Language Endpoint

```http
POST /v2/query
{
  "text": "Why did Panasonic exit plasma TV production?"
}
```

**Function Routing**:
- `search_similar(query_text: string, k: int=3)` → vector search
- `get_graph_neighbors(node_id: string, k: int=3)` → graph traversal

### 5.3. Response Schema

```json
{
  "intent": "why_decision",
  "evidence": {
    "anchor": {"id": "...", "option": "...", "rationale": "...", "tags": [...]},
    "events": [{"id": "...", "summary": "...", "timestamp": "...", "snippet": "..."}],
    "transitions": {"preceding": [], "succeeding": []},
    "allowed_ids": ["anchor-id", "event-1"]
  },
  "answer": {
    "short_answer": "...",  // ≤320 chars
    "supporting_ids": ["anchor-id", "event-1"]  // ⊆ allowed_ids
  },
  "completeness_flags": {
    "has_preceding": false,
    "has_succeeding": false,
    "event_count": 1
  },
  "meta": {
    "policy_id": "why_v1",
    "prompt_id": "why_v1.2",
    "prompt_fingerprint": "sha256:...",
    "snapshot_etag": "sha256:...",
    "fallback_used": false,
    "retries": 0,
    "latency_ms": 1250,
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
      "resolver_model_id": "bi_encoder_v1"
    },
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

## 6. Evidence Rules & Quotas

### 6.1. Intent Quotas (Enhanced)

| Intent | k-limit | Events | Preceding | Succeeding | Max Bundle Size | Notes |
|--------|---------|--------|-----------|------------|----------------|-------|
| `why_decision` | 1 | unbounded* | unbounded* | unbounded* | 8192 bytes | Core reasoning |
| `who_decided` | 1 | unbounded* | 0 | 0 | 8192 bytes | Decision makers |
| `when_decided` | 1 | unbounded* | unbounded* | unbounded* | 8192 bytes | Timeline context |
| `chains` | unlimited | unbounded* | N/A | N/A | 16384 bytes | Full chains |

*Subject to selector truncation when bundle exceeds max size; logs `selector_truncation=true` when evidence is dropped.

### 6.2. Evidence Size Management

#### 6.2.1. Truncation Behavior
- **Collection Phase**: Planner expands k=1 and collects ALL neighbors (unbounded)
- **Size Check**: If `json.dumps(bundle).length > MAX_PROMPT_BYTES`, selector truncates
- **Truncation Logic**: Drop lowest-scoring items until size fits prompt budget
- **Logging**: Set `selector_truncation=true` when evidence is dropped
- **ID Updates**: `allowed_ids` reflects final post-truncation evidence set

#### 6.2.2. Selector Model (Weak AI)
**Type**: GBDT/logistic regression for evidence scoring
**Features**: Text similarity, graph degree, recency delta, tag overlap
**Fallback**: Deterministic sort (recency + similarity) if model unavailable

### 6.3. Validation Rules

**Response Validation**:
- `supporting_ids ⊆ allowed_ids` (strict subset)
- `anchor.id` must appear in `supporting_ids`
- Present transition IDs must be cited

**Artifact Validation** (Ingest):

| Field | Rule | Purpose |
|-------|------|---------|
| `id` | `/^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$/` | Unique lookup (allows underscores) |
| `timestamp` | ISO-8601 UTC (`Z`) | Ordering/recency |
| Content fields* | ≥1 non-whitespace char | Prevents empty stubs |
| `tags` | Array of strings (optional) | Categorization |
| `x-extra` | Object (optional) | Extension field |

*Content fields: `rationale`, `description`, `reason`, `summary`, `snippet`

**Link Validation** (Updated):
If `supported_by`, `based_on`, `led_to`, `transitions`, `from`, or `to` **exist and are non-empty**, each ID must be resolvable; otherwise the field may be omitted or an empty array. This allows for orphaned events (no `led_to` yet) and standalone decisions (no `transitions`).

## 7. JSON Authoring Schemas

### 7.1. Decision (updated with new fields)
```json
{
  "id": "panasonic-exit-plasma-2012",
  "option": "Exit plasma TV production",
  "rationale": "Declining demand and heavy losses in plasma panels necessitated a strategic withdrawal to focus resources on automotive and battery growth.",
  "timestamp": "2012-04-30T09:00:00Z",
  "decision_maker": "Kazuhiro Tsuga",
  "tags": [
    "portfolio_rationalization",
    "loss_mitigation"
  ],
  "supported_by": [
    "pan-e2"
  ],
  "based_on": [
    "panasonic-tesla-battery-partnership-2010"
  ],
  "transitions": [
    "trans-pan-2010-2012",
    "trans-pan-2012-2014"
  ],
  "x-extra": {}
}
```

### 7.2. Event (updated with new fields)
```json
{
  "id": "pan-e4",
  "summary": "Board approves AU Automotive acquisition",
  "description": "In April 2014, Panasonic's board green-lit the €1.2 bn purchase of AU Automotive.",
  "timestamp": "2014-04-01T14:00:00Z",
  "tags": [
    "m_and_a",
    "automotive_electronics"
  ],
  "led_to": [
    "panasonic-automotive-infotainment-acquisition-2014"
  ],
  "snippet": "€1.2 bn for AU Automotive.",
  "x-extra": {}
}
```

### 7.3. Transition (updated with x-extra)
```json
{
  "id": "trans-pan-2010-2012",
  "from": "panasonic-tesla-battery-partnership-2010",
  "to": "panasonic-exit-plasma-2012",
  "relation": "causal",
  "reason": "Strategic focus shifted to EV batteries from plasma TVs",
  "timestamp": "2013-10-09T00:00:00Z",
  "tags": ["strategic_pivot"],
  "x-extra": {}
}
```

### 7.4. Orphan Handling Examples

**First Decision (no predecessor)**:
```json
{
  "id": "initial-cloud-decision-2024",
  "option": "Enter cloud market",
  "rationale": "Market opportunity identified...",
  "timestamp": "2024-01-15T10:00:00Z",
  "decision_maker": "Alice",
  "tags": ["strategic_expansion"],
  "supported_by": ["market-research-event"],
  "based_on": [],
  "transitions": [],
  "x-extra": {}
}
```

**Pending Event (no decision yet)**:
```json
{
  "id": "pending-security-audit",
  "summary": "Security audit reveals vulnerabilities",
  "description": "Annual security audit identified critical vulnerabilities...", 
  "timestamp": "2024-07-25T14:00:00Z",
  "tags": ["security", "compliance"],
  "led_to": [],
  "snippet": "Critical vulnerabilities found.",
  "x-extra": {}
}
```

## 8. Memory API Endpoints

- `GET /api/enrich/{type}/{id}` → normalized envelope
- `POST /api/graph/expand_candidates` → k=1 traversal (AQL)
- `POST /api/resolve/text` → vector search (ArangoDB)
- `GET /api/schema/fields` → field catalog
- `GET /api/schema/rels` → relation catalog

## 9. Weak AI Component Specifications

### 9.1. Resolver Models
**Bi-encoder**: `sentence-transformers/all-MiniLM-L6-v2` for decision similarity
**Cross-encoder**: `cross-encoder/ms-marco-MiniLM-L-6-v2` for reranking
**BM25 Fallback**: Always available lexical search when embeddings fail

### 9.2. Evidence Selector
**Purpose**: Score and rank evidence when truncation is needed
**Model Types**: GBDT (primary) → logistic regression → deterministic fallback
**Features**:
- `text_similarity`: cosine(event_text, anchor_rationale)  
- `graph_degree`: in/out degree of nodes
- `recency_delta`: days between event and anchor timestamps
- `tag_overlap`: intersection(event.tags, anchor.tags)

### 9.3. Graph Embeddings (Future)
**Methods**: Node2Vec or LightGCN for graph representation learning
**Dimensions**: 128d for graph structure features
**Purpose**: Enhanced similarity and recommendation features

## 10. Prompt & Audit System

### 10.1. Prompt Envelope
```json
{
  "prompt_version": "why_v1",
  "intent": "why_decision",
  "question": "Why did Panasonic exit plasma TV production?",
  "evidence": { /* minimal bundle */ },
  "allowed_ids": ["panasonic-exit-plasma-2012", "pan-e2"],
  "constraints": {
    "output_schema": "WhyDecisionAnswer@1",
    "max_tokens": 256
  }
}
```

### 10.2. Deterministic IDs
- `prompt_fingerprint`: SHA-256 of canonical envelope
- `snapshot_etag`: Content hash + timestamp of JSON corpus
- `bundle_fingerprint`: SHA-256 of evidence bundle
- `request_id`: Per-request trace ID

### 10.3. Artifact Retention & Audit

#### 10.3.1. Artifact Types (Per Request)
- **Prompt Envelope**: Canonical JSON with deterministic fingerprint
- **Rendered Prompt**: Exact string/bytes sent to LLM
- **Raw LLM JSON**: Unprocessed model output
- **Validator Report**: Schema validation results, ID scope checks
- **Final Response**: Complete response sent to client
- **Evidence Bundle**: Pre and post-truncation versions

#### 10.3.2. Storage Strategy
**Location**: MinIO/S3 with request_id-based keys
**Format**: `/{bucket}/{request_id}/{artifact_type}.json`
**Retention**: Configurable per tenant (default 14 days)
**Access Control**: `private|org|public` visibility levels

#### 10.3.3. Fingerprint Tracking
**Prompt Fingerprint**: SHA-256 of canonical envelope
**Bundle Fingerprint**: SHA-256 of evidence bundle
**Request Linking**: Chain related requests via fingerprints

## 11. Performance & Reliability

### 11.1. Performance Targets
- **TTFB**: ≤600ms (known slug), ≤2.5s (search)
- **p95 Total**: ≤3.0s (`/v2/ask`), ≤4.5s (`/v2/query`)
- **Model Inference**: ≤5ms (resolver), ≤2ms (selector)

### 11.2. Fallback Strategy
1. **LLM Failures**: Auto-retry ≤2 times → templated answer
2. **Model Failures**: Weak AI → deterministic methods
3. **Timeout**: Return partial evidence with `completeness_flags`

### 11.3. Caching
- **Resolver**: 5min TTL
- **Evidence Bundle**: 15min TTL, invalidate on `snapshot_etag` change
- **LLM JSON**: 2min TTL for hot anchors
- **Graph Expansion**: 1min TTL for k=1 results

### 11.4. Load Shedding & Circuit Breakers

#### 11.4.1. Auto Load-Shedding
**Trigger Conditions**:
- Queue depth > threshold
- Stage timeouts exceeded
- External service failures

**Actions**:
- Set `llm_mode=off` (switch to templater)
- Skip expensive operations (embeddings, complex graph traversals)
- Return `meta.load_shed=true` in responses

#### 11.4.2. Circuit Breaker States
**Closed**: Normal operation, all features enabled
**Open**: Failures detected, fallback to basic operations
**Half-Open**: Testing recovery, gradual feature re-enablement

## 12. Configuration & Intent Registry

**Intent Registry**: Policy-as-data configuration via `registry.json` (JSON format for consistency, not watched by snapshot watcher)

**Environment**: `SNAPSHOT_EXT=json`, watcher globs `**/*.json`

**Feature Flags**:
```python
ENABLE_EMBEDDINGS = True          # Vector search vs BM25 only
ENABLE_SELECTOR_MODEL = True      # Learned vs deterministic selection  
ENABLE_GRAPH_EMBEDDINGS = False   # Graph ML features
ENABLE_LOAD_SHEDDING = True       # Auto fallback under load
ENABLE_ARTIFACT_RETENTION = True  # Store audit artifacts
ENABLE_CACHING = True             # Redis caching layer
```

## 13. Testing Framework

### 13.1. Golden Test Structure
```
/tests/golden/
  why_decision_panasonic_plasma.json      # Core plasma TV exit case
  why_decision_with_based_on.json         # Decision chains
  why_decision_tags_filtering.json        # Tag-based evidence
  who_decided_anchor_v1.json              # Decision maker identification  
  when_decided_anchor_v1.json             # Timeline reconstruction
  event_with_snippet_display.json         # Snippet field usage
```

### 13.2. Test Requirements
**Coverage**: `coverage = 1.0` and `completeness_debt = 0` on all golden tests
**Reproducibility**: Identical results across runs with same input
**Performance**: All golden tests must meet latency SLOs

### 13.3. Validation Tests
**Schema Tests**: All response schemas validated
**Cross-link Tests**: Bidirectional relationship enforcement  
**Orphan Tests**: Isolated events/decisions handled gracefully
**New Field Tests**: Tags, snippets, x-extra processing

## 14. Acceptance Criteria

### 14.1. Functional Requirements
1. `/v2/ask` with known slug returns in ≤3.0s p95, `fallback_used=false`
2. `/v2/query` natural language routing works for basic intents
3. Invalid LLM JSON triggers fallback, never returns user-visible errors
4. New JSON fields auto-appear in `/v2/schema/fields` (zero code changes)

### 14.2. Quality Gates
5. Golden tests pass: `coverage=1.0`, `completeness_debt=0`
6. All responses include audit metadata: `prompt_id`, `policy_id`, `prompt_fingerprint`
7. Complete artifact retention for every request
8. Health endpoints ready with ArangoDB dependency checks

### 14.3. Data Quality & Orphan Handling
9. All timestamps in UTC (`Z`) format
10. Event summary auto-repair if missing/empty
11. Cross-link reciprocity: `event.led_to ↔ decision.supported_by` and `decision.based_on ↔ prior_decision.transitions` (where both exist)
12. k=1 expansion collects all neighbours; gateway may truncate for prompt-size or latency budgets (logs `selector_truncation=true`)
13. Orphaned events and decisions are valid and handled gracefully
14. Empty link arrays (`[]`) are valid; omitted fields are treated as empty arrays
15. Tags and snippets are optional enrichment fields
16. The `x-extra` field provides extensibility without schema changes

## 15. Key Schema Changes Summary

### 15.1. New Fields Added
- **Decision**: `tags`, `based_on`, `x-extra`
- **Event**: `tags`, `snippet`, `x-extra` 
- **Transition**: `tags`, `x-extra`

### 15.2. Field Purpose Updates
- **`based_on`**: References to prior decisions that influenced this decision (complements `supported_by` events)
- **`tags`**: Categorical labels for filtering and grouping
- **`snippet`**: Brief extract for display in evidence bundles
- **`x-extra`**: Extension object for custom fields without schema migration

### 15.3. Validation Updates
- ID regex allows underscores: `/^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$/`
- `snippet` added to content field validation
- Cross-link reciprocity extended to `based_on ↔ transitions` relationships

## 16. Service Structure

```
/services
  /api-edge          # HTTP gateway, auth, rate limiting
  /gateway           # Core orchestration, evidence bundling
    /intent_router/  # Natural language → function routing
    /resolver/       # Weak AI anchor resolution
    /evidence/       # Bundle building with ML selection
    /validator/      # Response validation
  /memory-api        # Graph ops, enrichment, schema catalog
  /ingest           # JSON validation, normalization, ArangoDB
/packages
  /core-storage     # ArangoDB graph + vector adapters
  /core-models      # Pydantic schemas
  /core-logging     # Structured logging with OTEL
  /core-config      # Configuration management
  /core-errors      # Error handling
  /core-ids         # Deterministic ID generation
/memory
  /{decisions,events,transitions}/*.json
```

## 17. Development Setup

**Prerequisites**: Docker Compose with ArangoDB, Redis, MinIO

**Quick Start**:
```bash
docker-compose up -d
./scripts/seed_memory.sh  # Load /memory/*.json
./scripts/smoke.sh        # E2E health check
```

**Environment**: `SNAPSHOT_EXT=json`, watcher globs `**/*.json`

## 18. Production Readiness

### 18.1. Monitoring & Alerting
**SLOs**: TTFB, total latency, fallback rate, cache hit rate
**Alerting**: Breach notifications, model drift detection
**Dashboards**: Stage timings, evidence truncation rates, model performance

### 18.2. Security & Privacy
- **Authentication**: Bearer/JWT tokens at API edge
- **CORS**: Configurable allow-list for frontend origins
- **PII Handling**: Best-effort redaction in audit artifacts
- **Data Retention**: Configurable per-tenant policies

### 18.3. Production Checklist
- [ ] All golden tests passing at 100% coverage
- [ ] Performance SLOs met under realistic load
- [ ] Complete monitoring and alerting configured
- [ ] Security review completed
- [ ] Documentation complete and validated
- [ ] Disaster recovery procedures tested

## 19. Implementation Test Cases

### 19.1. Validator Unit Tests
- `decision_no_transitions.json` (empty array validation)
- `event_orphan.json` (no `led_to` validation)
- `decision_with_tags.json` (tags array validation)
- `decision_based_on_validation.json` (based_on link validation)
- `event_with_snippet.json` (snippet field validation)

### 19.2. Golden Test Cases
- `why_decision_panasonic_plasma.json` (plasma TV exit with automotive pivot context)
- `why_decision_with_based_on.json` (decision influenced by prior decisions)
- `why_decision_tags_filtering.json` (evidence filtering by tags)
- `event_with_snippet_display.json` (snippet field in evidence bundle)

### 19.3. Router Contract Tests
- `test_query_panasonic.py` → text: "Why did Panasonic exit plasma?" → expects Memory API calls + final WhyDecisionResponse@1 body
- Cross-link validation for `based_on` relationships

### 19.4. Back-link Derivation Tests
- Bidirectional repair for `based_on ↔ transitions` relationships
- Tag-based evidence enrichment and filtering

This updated specification provides complete alignment with the technical specification and requirements checklist, incorporating all performance budgets, weak AI components, enhanced observability, and comprehensive testing requirements.