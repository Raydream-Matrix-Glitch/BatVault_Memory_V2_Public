# V2 Batvault Memory - Implementation Requirements Checklist (Updated & Aligned)

## A. Core API & Endpoints

### API Edge Service
- [ ] `POST /v2/ask` endpoint with structured intent-based queries
- [ ] `POST /v2/query` endpoint with natural language input and LLM function routing
- [ ] HTTP middleware: auth, rate-limiting, idempotency, CORS
- [ ] Idempotency: Keys + request hash → dedupe concurrent identical calls (Redis/LFU guard)
- [ ] Bearer/JWT authentication at API edge
- [ ] CORS allow-list for frontend origins
- [ ] Request/response envelope schemas (Pydantic/OpenAPI)
- [ ] Health endpoints: `GET /healthz` (process up) and `GET /readyz` (dependencies ready)

### Gateway Service
- [ ] Intent resolution and routing system
- [ ] LLM function routing for natural language queries with specific functions:
  - [ ] `search_similar(query_text: string, k: int=3)` → Memory API text resolution endpoint
  - [ ] `get_graph_neighbors(node_id: string, k: int=3)` → Memory API graph expansion endpoint
- [ ] Evidence planner with schema-agnostic Graph Query Plan compilation
- [ ] Evidence bundle builder with k=1 neighbor collection
- [ ] Evidence size management with constants:
  - [ ] `MAX_PROMPT_BYTES = 8192` (hard limit for bundle size)
  - [ ] `SELECTOR_TRUNCATION_THRESHOLD = 6144` (start truncating before hard limit)
  - [ ] `MIN_EVIDENCE_ITEMS = 1` (always keep anchor + 1 supporting item)
- [ ] Prompt envelope builder (canonical JSON, versioned)
- [ ] LLM client with JSON-only mode, retries, validation
- [ ] Validator with blocking schema/ID scope checks
- [ ] Renderer for streaming tokenized responses
- [ ] Fallback templater for deterministic answers

## B. Data Models & Schemas

### JSON Schema Definitions
- [ ] `WhyDecisionEvidence@1` schema
- [ ] `WhyDecisionAnswer@1` schema  
- [ ] `WhyDecisionResponse@1` schema
- [ ] Error envelope schema with consistent error codes
- [ ] Intent registry (data-driven configuration)
- [ ] Intent quota table with k-limits for all intents:

| Intent | K-limit | Scope |
|--------|---------|-------|
| `why_decision` | k=1 | Unbounded events + transitions |
| `who_decided` | k=1 | Decision makers + context |
| `when_decided` | k=1 | Timeline + related decisions |

- [ ] Prompt envelope schema (versioned, auditable)

### Response Contracts
- [ ] Evidence bundle: `{anchor, events[], transitions{preceding|succeeding}, allowed_ids[]}`
- [ ] Answer object: `{short_answer, supporting_ids[]}`
- [ ] Meta object: `{policy_id, prompt_id, retries, latency_ms, fallback_used, function_calls[], routing_confidence}`
- [ ] Completeness flags: `{has_preceding, has_succeeding, event_count}`

## C. Storage & Data Layer

### ArangoDB Setup
- [ ] Graph collections for decisions, events, transitions
- [ ] Vector indexes with SIM_DIM = 768 dimensions for HNSW
- [ ] AQL query compilation for k=1 traversals
- [ ] Graph operations (upsert nodes/edges)
- [ ] Vector search operations
- [ ] Connection health checks and readiness probes

### Memory API Service
- [ ] Decision enrichment endpoint - normalized decision envelopes
- [ ] Event enrichment endpoint - normalized event envelopes
- [ ] Transition enrichment endpoint - normalized transition envelopes
- [ ] Graph expansion endpoint - k=1 neighborhood expansion
- [ ] Text resolution endpoint - vector similarity search
- [ ] Field catalog endpoint - field catalog endpoint
- [ ] Relation catalog endpoint - relation catalog endpoint

## D. Ingest Pipeline

### JSON Processing Pipeline
- [ ] File watcher with snapshot ETag generation
- [ ] JSON parsing with file/line diagnostics
- [ ] Strict validation with ID regex: `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$` (allows underscores)
- [ ] Artifact validation (ID, timestamp, content field requirements)
- [ ] Normalization/aliasing (schema-agnostic field mapping)
- [ ] Text processing (NFKC, trim, collapse whitespace, length limits)
- [ ] Timestamp parsing to ISO-8601 UTC
- [ ] Tag processing (lowercase, slugify, dedupe, sort)
- [ ] New field validation: `tags[]`, `based_on[]`, `snippet`, `x-extra{}`

### Data Derivation
- [ ] Backlink enforcement (`event.led_to ↔ decision.supported_by`)
- [ ] Extended cross-references (`decision.based_on ↔ prior_decision.transitions`)
- [ ] Transition cross-references in related decisions
- [ ] Field catalog generation (semantic names → aliases)
- [ ] Relation catalog generation (available edge types)
- [ ] Event summary repair (derive from description if missing)

### Persistence
- [ ] ArangoDB node/edge upserts
- [ ] Vector index building/refreshing
- [ ] Content-addressable snapshot storage
- [ ] BM25 text indexing
- [ ] Adjacency list generation

## E. Weak AI Components

### Resolver Models
- [ ] Bi-encoder for decision similarity search
- [ ] Cross-encoder for reranking ambiguous cases
- [ ] BM25 lexical search fallback
- [ ] Anchor resolution with precedence rules (slug → skip search)

### Evidence Selection
- [ ] Learned scorer for event/transition selection (GBDT/logistic regression)
- [ ] Feature extraction (text similarity, graph features, tag overlap)
- [ ] Evidence truncation with size management:
  - [ ] MAX_PROMPT_BYTES = 8192 (hard limit for bundle size)
  - [ ] SELECTOR_TRUNCATION_THRESHOLD = 6144 (start truncating before hard limit)
  - [ ] MIN_EVIDENCE_ITEMS = 1 (always keep anchor + 1 supporting item)
  - [ ] SIM_DIM = 768 (vector dimension for HNSW index)
- [ ] Deterministic fallback (recency + similarity sorting)

### Graph Embeddings
- [ ] Node2Vec/LightGCN embeddings for graph representation learning
- [ ] Vector similarity computation for related content discovery

## F. LLM Integration

### LLM Client
- [ ] JSON-only mode enforcement
- [ ] Temperature=0 for deterministic outputs
- [ ] Token limits and budget management
- [ ] Retry logic (≤2 retries) with exponential backoff
- [ ] Raw response capturing for audit

### Function Routing System
- [ ] Natural language → function call mapping
- [ ] Function definitions:
  - [ ] `search_similar(query_text: string, k: int=3)`
  - [ ] `get_graph_neighbors(node_id: string, k: int=3)`
- [ ] Routing confidence scoring and logging
- [ ] Memory API integration for function execution
- [ ] Result merging from multiple function calls

### Validation System
- [ ] Schema validation against answer schemas
- [ ] ID scope validation (`supporting_ids ⊆ allowed_ids`)
- [ ] Mandatory ID enforcement (anchor + present transitions)
- [ ] Blocking validation with deterministic fallback

## G. Observability & Audit

### Structured Logging
- [ ] OpenTelemetry spans across all stages
- [ ] Deterministic request IDs and fingerprints
- [ ] Stage-specific metadata (resolver confidence, selector features)
- [ ] Evidence bundling metrics with complete field set:
  - [ ] `total_neighbors_found` - count before any filtering
  - [ ] `selector_truncation` - boolean flag when evidence dropped
  - [ ] `final_evidence_count` - count after truncation
  - [ ] `dropped_evidence_ids[]` - IDs of items removed
  - [ ] `bundle_size_bytes` - final bundle size
  - [ ] `max_prompt_bytes` - configured limit (8192)
- [ ] Function routing metrics:
  - [ ] `function_calls[]` - list of called functions
  - [ ] `routing_confidence` - LLM routing accuracy score
  - [ ] `routing_model_id` - model used for function routing
- [ ] Snapshot ETag tracking in all logs

### Artifact Retention
- [ ] Query & resolver results storage
- [ ] Graph plan persistence
- [ ] Evidence bundle storage (pre/post limits)
- [ ] Prompt envelope archival
- [ ] Raw LLM JSON retention
- [ ] Validator report storage
- [ ] Final response JSON archival
- [ ] Function routing trace storage
- [ ] Artifact storage in MinIO/S3 with request_id keys

### Metrics & Monitoring
- [ ] TTFB and total latency tracking
- [ ] Retry and fallback usage metrics
- [ ] Coverage and completeness scoring
- [ ] Cache hit rate monitoring
- [ ] Weak AI model performance metrics
- [ ] Function routing accuracy metrics
- [ ] Dashboards for latency SLOs and error rates
- [ ] Alerts for fallback spikes and model drift

## H. Performance & Reliability

### Performance Requirements
- [ ] `/v2/ask` p95 latency ≤3.0s for known slugs
- [ ] `/v2/query` p95 latency ≤4.5s for natural language
- [ ] TTFB ≤600ms (slug) / ≤2.5s (search)
- [ ] Stage timeouts: Search 800ms, Graph 250ms, Enrich 600ms, LLM 1500ms, Validator 300ms
- [ ] Model inference: ≤5ms (resolver), ≤2ms (selector)

### Caching Strategy
- [ ] Resolver cache (5min TTL, normalized decision_ref keys)
- [ ] Evidence bundle cache (15min TTL, invalidate on snapshot ETag change)
- [ ] LLM JSON cache (2min TTL for hot anchors)
- [ ] Redis integration for distributed caching

### Load Shedding
- [ ] Auto load-shedding to templater mode under stress
- [ ] Circuit breakers for external dependencies
- [ ] Queue depth monitoring and throttling
- [ ] `meta.load_shed=true` flag in responses

## I. Quality Assurance

### Testing Requirements
- [ ] Golden tests for Why/Who/When intents with named fixtures:
  - [ ] Plasma TV exit with automotive pivot context
  - [ ] Decision influenced by prior decisions
  - [ ] Evidence filtering by tags
  - [ ] Snippet field in evidence bundle
  - [ ] Decision maker identification
  - [ ] Timeline reconstruction
  - [ ] Empty array validation
  - [ ] Orphan entity validation
  - [ ] Tags array validation
  - [ ] Based_on link validation
- [ ] Coverage = 1.0 and completeness_debt = 0 on fixtures
- [ ] Unit tests for all components
- [ ] Integration tests for service boundaries
- [ ] Contract tests for API compatibility
- [ ] End-to-end tests in Docker Compose environment

### Function Routing Tests
- [ ] Router contract tests:
  - [ ] Natural language decision queries → Memory API calls + appropriate response schema
  - [ ] Natural language → `search_similar()` calls
  - [ ] Node queries → `get_graph_neighbors()` calls
  - [ ] LLM function routing accuracy testing
- [ ] Function call validation tests
- [ ] Memory API integration tests for routed calls

### Validation Gates
- [ ] Schema-agnostic proof: new JSON fields appear without code changes
- [ ] Function routing correctly maps natural language to Memory API calls
- [ ] All timestamps returned as ISO-8601 UTC
- [ ] Cross-link reciprocity enforcement (including `based_on ↔ transitions`)
- [ ] Catalog endpoints reflect current JSON structure
- [ ] Orphan handling validation (isolated events, decisions without predecessors)

## J. JSON Authoring Schemas

### Decision Schema
- [ ] ID validation with regex: `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$` (allows underscores)
- [ ] Required fields: id, option, rationale, timestamp, decision_maker
- [ ] Optional fields: tags, supported_by, based_on, transitions
- [ ] New field validation: tags[] (array of strings), based_on[] (array of decision IDs)
- [ ] x-extra extensibility field

### Event Schema
- [ ] Required fields: id, summary, description, timestamp
- [ ] Optional fields: tags, led_to, snippet
- [ ] Summary repair logic when missing or equals ID
- [ ] New field validation: tags[] (array of strings), snippet (string ≤120 chars)
- [ ] x-extra extensibility field

### Transition Schema
- [ ] Required fields: id, from, to, relation, reason, timestamp
- [ ] Relation enum validation (causal, alternative, chain_next)
- [ ] Optional fields: tags
- [ ] New field validation: tags[] (array of strings)
- [ ] x-extra extensibility field

## K. Infrastructure & Deployment

### Technology Stack
- [ ] Python 3.11 services (FastAPI/Uvicorn)
- [ ] Node 20 frontend (Next.js/React)
- [ ] ArangoDB Community Edition
- [ ] Redis 7 for caching
- [ ] MinIO for artifact storage

### Containerization
- [ ] Dockerfiles for each service
- [ ] Docker Compose configuration with all services
- [ ] ArangoDB service configuration
- [ ] MinIO artifact store bootstrapping
- [ ] OpenTelemetry collector configuration

### Development Environment
- [ ] `docker-compose up` → working system in <5 minutes
- [ ] Seed data loading script
- [ ] Smoke test script
- [ ] Hot reload for development

## L. Advanced Features

### Streaming & Real-time
- [ ] Server-sent events (SSE) for response streaming
- [ ] Progressive token rendering
- [ ] Frontend audit drawer with trace viewer
- [ ] Real-time completeness flag updates
- [ ] Function routing trace visualization

### Security & Privacy
- [ ] Request fingerprinting and deduplication
- [ ] PII redaction in prompt envelopes
- [ ] Reversible hash salts per request
- [ ] Tenant-level artifact retention policies (`retention_days` default 14)
- [ ] Artifact visibility controls (`private|org|public`)

### Configuration Management
- [ ] Feature flags for per-intent rollouts
- [ ] A/B testing harness at policy layer
- [ ] Intent registry as data-driven configuration
- [ ] Environment-specific configuration management
- [ ] Function routing model configuration

## M. Documentation & API

### API Documentation
- [ ] OpenAPI specification generation
- [ ] Interactive API documentation
- [ ] Schema documentation with examples
- [ ] Error code reference guide
- [ ] Function routing documentation

### Developer Experience
- [ ] Clear project structure with service boundaries
- [ ] Comprehensive README with setup instructions
- [ ] API client examples and SDKs
- [ ] Troubleshooting guides and runbooks
- [ ] Function routing integration guide

## N. Orphan Handling & New Fields

### Orphan Data Support
- [ ] Events without `led_to` are valid (pending decisions)
- [ ] Decisions without `transitions` are valid (isolated/initial decisions)
- [ ] Empty arrays valid; missing fields treated as empty arrays
- [ ] Validation only enforces links when arrays are non-empty
- [ ] Orphan data visualization in frontend

### Extended Schema Support
- [ ] `tags[]` field processing and validation across all entity types
- [ ] `based_on[]` field for decision dependencies
- [ ] `snippet` field for brief excerpts in events
- [ ] `x-extra{}` extensibility object for custom fields
- [ ] Cross-link reciprocity for `based_on ↔ transitions` relationships
- [ ] Tag-based filtering and evidence selection

### Field Catalog Integration
- [ ] Live field catalog generation from JSON structure
- [ ] Semantic name → alias mapping
- [ ] Schema-agnostic field access in evidence builder
- [ ] Real-time catalog updates on schema changes

## O. Enhanced Testing Requirements

### Golden Test Coverage
- [ ] Named test fixtures covering all scenarios:
  - [ ] Basic why/who/when decision queries
  - [ ] Orphan data handling (isolated events, decisions)
  - [ ] New field validation (tags, based_on, snippet, x-extra)
  - [ ] Cross-link validation and repair
  - [ ] Evidence truncation scenarios
  - [ ] Function routing accuracy
- [ ] 100% golden test coverage with completeness_debt = 0

### Performance Testing
- [ ] Load testing with evidence truncation scenarios
- [ ] Function routing latency testing
- [ ] Cache performance under realistic loads
- [ ] Stage timeout enforcement validation
- [ ] Memory usage profiling with large evidence bundles

### Integration Testing
- [ ] Full docker-compose environment testing
- [ ] ArangoDB integration with vector search
- [ ] Redis caching behavior validation
- [ ] MinIO artifact storage integration
- [ ] End-to-end SSE streaming validation

## Success Criteria

### Final Acceptance
- [ ] All golden tests pass with 100% coverage
- [ ] Performance requirements met under load testing:
  - [ ] p95 ≤3.0s for `/v2/ask` with known slugs
  - [ ] p95 ≤4.5s for `/v2/query` with natural language
  - [ ] TTFB ≤600ms (slug) / ≤2.5s (search)
  - [ ] Stage timeouts: Search 800ms, Graph 250ms, Enrich 600ms, LLM 1500ms, Validator 300ms
- [ ] Complete audit trail for 100% of requests
- [ ] Schema-agnostic functionality demonstrated
- [ ] Function routing accuracy >90% for natural language queries
- [ ] Fallback rate <5% under normal operations
- [ ] Evidence size management working correctly:
  - [ ] Truncation only when bundle > MAX_PROMPT_BYTES (8192)
  - [ ] `selector_truncation=true` logged when evidence dropped
  - [ ] Minimum evidence items preserved (MIN_EVIDENCE_ITEMS=1)
- [ ] End-to-end Docker Compose deployment working
- [ ] Production readiness checklist completed
- [ ] New field support validated (tags, based_on, snippet, x-extra)
- [ ] Orphan data handling working correctly
- [ ] Cross-link reciprocity enforced for all relationship types