# V2 Batvault Memory - Requirements to Milestone Mapping

## Overview
This document maps all requirements from the Implementation Requirements Checklist to the specific development milestones, ensuring comprehensive coverage and proper sequencing.

---

## Milestone 0 — Bootstrapping & Health (Day 1-2)

### A. Core API & Endpoints
- [ ] HTTP middleware: auth, rate-limiting, idempotency, CORS
- [ ] Bearer/JWT authentication at API edge
- [ ] CORS allow-list for frontend origins
- [ ] Health endpoints: `GET /healthz` (process up) and `GET /readyz` (dependencies ready)
- [ ] Idempotency: Keys + request hash → dedupe concurrent identical calls (Redis/LFU guard)

### B. Data Models & Schemas
- [ ] Error envelope schema with consistent error codes
- [ ] Response Contracts (basic structure)

### C. Storage & Data Layer
- [ ] ArangoDB Setup (basic connection, health checks)
- [ ] Connection health checks and readiness probes

### G. Observability & Audit
- [ ] OpenTelemetry spans across all stages (basic setup)
- [ ] Deterministic request IDs and fingerprints
- [ ] Structured Logging (framework setup)

### H. Performance & Reliability
- [ ] Circuit breakers for external dependencies (basic setup)

### K. Infrastructure & Deployment
- [ ] Python 3.11 services (FastAPI/Uvicorn)
- [ ] ArangoDB Community Edition
- [ ] Redis 7 for caching
- [ ] MinIO for artifact storage
- [ ] Dockerfiles for each service
- [ ] Docker Compose configuration with all services
- [ ] Development Environment: `docker-compose up` → working system in <5 minutes

---

## Milestone 1 — Ingest V2 + Catalogs + Core Storage (Day 3-5)

### D. Ingest Pipeline
- [ ] File watcher with snapshot ETag generation
- [ ] JSON parsing with file/line diagnostics
- [ ] Strict validation with ID regex: `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$` (allows underscores)
- [ ] Artifact validation (ID, timestamp, content field requirements)
- [ ] Normalization/aliasing (schema-agnostic field mapping)
- [ ] Text processing (NFKC, trim, collapse whitespace, length limits)
- [ ] Timestamp parsing to ISO-8601 UTC
- [ ] Tag processing (lowercase, slugify, dedupe, sort)
- [ ] New field validation: `tags[]`, `based_on[]`, `snippet`, `x-extra{}`
- [ ] Backlink enforcement (`event.led_to ↔ decision.supported_by`)
- [ ] Extended cross-references (`decision.based_on ↔ prior_decision.transitions`)
- [ ] Transition cross-references in related decisions
- [ ] Field catalog generation (semantic names → aliases)
- [ ] Relation catalog generation (available edge types)
- [ ] Event summary repair (derive from description if missing)
- [ ] ArangoDB node/edge upserts
- [ ] Content-addressable snapshot storage
- [ ] Adjacency list generation

### C. Storage & Data Layer
- [ ] Graph collections for decisions, events, transitions
- [ ] Vector indexes with SIM_DIM = 768 dimensions for HNSW
- [ ] Graph operations (upsert nodes/edges)

### C. Storage & Data Layer - Memory API Service
- [ ] Decision enrichment endpoint - normalized decision envelopes
- [ ] Event enrichment endpoint - normalized event envelopes
- [ ] Transition enrichment endpoint - normalized transition envelopes
- [ ] Field catalog endpoint - field catalog endpoint
- [ ] Relation catalog endpoint - relation catalog endpoint

### J. JSON Authoring Schemas
- [ ] Decision Schema (ID validation, required/optional fields, new fields)
- [ ] Event Schema (required/optional fields, summary repair, new fields)
- [ ] Transition Schema (required fields, relation enum, new fields)

### N. Orphan Handling & New Fields
- [ ] Events without `led_to` are valid (pending decisions)
- [ ] Decisions without `transitions` are valid (isolated/initial decisions)
- [ ] Empty arrays valid; missing fields treated as empty arrays
- [ ] Validation only enforces links when arrays are non-empty
- [ ] `tags[]` field processing and validation across all entity types
- [ ] `based_on[]` field for decision dependencies
- [ ] `snippet` field for brief excerpts in events
- [ ] `x-extra{}` extensibility object for custom fields
- [ ] Cross-link reciprocity for `based_on ↔ transitions` relationships
- [ ] Tag-based filtering and evidence selection
- [ ] Live field catalog generation from JSON structure
- [ ] Semantic name → alias mapping
- [ ] Schema-agnostic field access in evidence builder
- [ ] Real-time catalog updates on schema changes

---

## Milestone 2 — Memory API k=1 + Resolver + Caching (Day 6-8)

### A. Core API & Endpoints - Gateway Service
- [ ] Intent resolution and routing system
- [ ] Evidence planner with schema-agnostic Graph Query Plan compilation
- [ ] Evidence bundle builder with k=1 neighbor collection

### C. Storage & Data Layer
- [ ] AQL query compilation for k=1 traversals
- [ ] Vector search operations

### C. Storage & Data Layer - Memory API Service
- [ ] Graph expansion endpoint - k=1 neighborhood expansion
- [ ] Text resolution endpoint - vector similarity search

### E. Weak AI Components - Resolver Models
- [ ] Bi-encoder for decision similarity search
- [ ] BM25 lexical search fallback
- [ ] Anchor resolution with precedence rules (slug → skip search)

### H. Performance & Reliability - Caching Strategy
- [ ] Resolver cache (5min TTL, normalized decision_ref keys)
- [ ] Evidence bundle cache (15min TTL, invalidate on snapshot ETag change)
- [ ] Redis integration for distributed caching

### H. Performance & Reliability - Performance Requirements
- [ ] Stage timeouts: Search 800ms, Graph 250ms, Enrich 600ms
- [ ] Model inference: ≤5ms (resolver)

---

## Milestone 3 — Gateway Evidence + Validator + Weak AI (Day 9-12)

### A. Core API & Endpoints - Gateway Service
- [ ] Evidence size management with constants:
  - [ ] `MAX_PROMPT_BYTES = 8192` (hard limit for bundle size)
  - [ ] `SELECTOR_TRUNCATION_THRESHOLD = 6144` (start truncating before hard limit)
  - [ ] `MIN_EVIDENCE_ITEMS = 1` (always keep anchor + 1 supporting item)
- [ ] Prompt envelope builder (canonical JSON, versioned)
- [ ] Validator with blocking schema/ID scope checks
- [ ] Fallback templater for deterministic answers

### B. Data Models & Schemas
- [ ] `WhyDecisionEvidence@1` schema
- [ ] `WhyDecisionAnswer@1` schema
- [ ] `WhyDecisionResponse@1` schema
- [ ] Response Contracts (complete implementation)
- [ ] Prompt envelope schema (versioned, auditable)

### E. Weak AI Components - Evidence Selection
- [ ] Learned scorer for event/transition selection (GBDT/logistic regression)
- [ ] Feature extraction (text similarity, graph features, tag overlap)
- [ ] Evidence truncation with size management
- [ ] Deterministic fallback (recency + similarity sorting)

### E. Weak AI Components - Graph Embeddings
- [ ] Node2Vec/LightGCN embeddings for graph representation learning
- [ ] Vector similarity computation for related content discovery

### G. Observability & Audit - Structured Logging
- [ ] Stage-specific metadata (resolver confidence, selector features)
- [ ] Evidence bundling metrics with complete field set:
  - [ ] `total_neighbors_found` - count before any filtering
  - [ ] `selector_truncation` - boolean flag when evidence dropped
  - [ ] `final_evidence_count` - count after truncation
  - [ ] `dropped_evidence_ids[]` - IDs of items removed
  - [ ] `bundle_size_bytes` - final bundle size
  - [ ] `max_prompt_bytes` - configured limit (8192)
- [ ] Snapshot ETag tracking in all logs

### G. Observability & Audit - Artifact Retention
- [ ] Query & resolver results storage
- [ ] Graph plan persistence
- [ ] Evidence bundle storage (pre/post limits)
- [ ] Prompt envelope archival
- [ ] Validator report storage
- [ ] Artifact storage in MinIO/S3 with request_id keys

---

## Milestone 4 — /v2/ask + /v2/query + LLM Integration (Day 13-16)

### A. Core API & Endpoints
- [ ] `POST /v2/ask` endpoint with structured intent-based queries
- [ ] `POST /v2/query` endpoint with natural language input and LLM function routing
- [ ] Request/response envelope schemas (Pydantic/OpenAPI)

### A. Core API & Endpoints - Gateway Service
- [ ] LLM function routing for natural language queries with specific functions:
  - [ ] `search_similar(query_text: string, k: int=3)` → Memory API text resolution endpoint
  - [ ] `get_graph_neighbors(node_id: string, k: int=3)` → Memory API graph expansion endpoint
- [ ] LLM client with JSON-only mode, retries, validation
- [ ] Renderer for streaming tokenized responses

### B. Data Models & Schemas
- [ ] Intent registry (data-driven configuration)
- [ ] Intent quota table with k-limits for all intents
- [ ] Meta object: `{policy_id, prompt_id, retries, latency_ms, fallback_used, function_calls[], routing_confidence}`
- [ ] Completeness flags: `{has_preceding, has_succeeding, event_count}`

### F. LLM Integration
- [ ] LLM Client (JSON-only mode enforcement, Temperature=0, Token limits, Retry logic)
- [ ] Function Routing System:
  - [ ] Natural language → function call mapping
  - [ ] Function definitions for search_similar and get_graph_neighbors
  - [ ] Routing confidence scoring and logging
  - [ ] Memory API integration for function execution
  - [ ] Result merging from multiple function calls
- [ ] Validation System (Schema validation, ID scope validation, Mandatory ID enforcement)

### G. Observability & Audit
- [ ] Function routing metrics:
  - [ ] `function_calls[]` - list of called functions
  - [ ] `routing_confidence` - LLM routing accuracy score
  - [ ] `routing_model_id` - model used for function routing
- [ ] Raw LLM JSON retention
- [ ] Final response JSON archival
- [ ] Function routing trace storage

### H. Performance & Reliability
- [ ] `/v2/ask` p95 latency ≤3.0s for known slugs
- [ ] `/v2/query` p95 latency ≤4.5s for natural language
- [ ] TTFB ≤600ms (slug) / ≤2.5s (search)
- [ ] Stage timeouts: LLM 1500ms, Validator 300ms
- [ ] LLM JSON cache (2min TTL for hot anchors)
- [ ] Auto load-shedding to templater mode under stress
- [ ] Queue depth monitoring and throttling
- [ ] `meta.load_shed=true` flag in responses

### L. Advanced Features - Streaming & Real-time
- [ ] Server-sent events (SSE) for response streaming
- [ ] Progressive token rendering

---

## Milestone 5 — Frontend App + Audit Interface (Day 17-19)

### K. Infrastructure & Deployment
- [ ] Node 20 frontend (Next.js/React)

### L. Advanced Features - Streaming & Real-time
- [ ] Frontend audit drawer with trace viewer
- [ ] Real-time completeness flag updates
- [ ] Function routing trace visualization

### L. Advanced Features - Security & Privacy
- [ ] Request fingerprinting and deduplication
- [ ] PII redaction in prompt envelopes
- [ ] Reversible hash salts per request

### M. Documentation & API
- [ ] Clear project structure with service boundaries
- [ ] API client examples and SDKs

### T. Frontend & User Experience Updates
- [ ] Evidence Display (Tags, Snippets, Based-on links, Orphan indicators)
- [ ] Audit Interface (Request trace, Prompt viewer, Evidence inspector, etc.)
- [ ] Advanced Features (Schema explorer, Decision graph, Tag cloud, etc.)
- [ ] API Integration (CORS, Auth, Error boundaries, Loading states)

---

## Milestone 6 — Harden + Golden Suites + Production Ready (Day 20-22)

### I. Quality Assurance
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
- [ ] Function Routing Tests (Router contract tests, Function call validation, Memory API integration)
- [ ] Validation Gates (Schema-agnostic proof, Cross-link reciprocity, Catalog endpoints, etc.)

### G. Observability & Audit - Metrics & Monitoring
- [ ] TTFB and total latency tracking
- [ ] Retry and fallback usage metrics
- [ ] Coverage and completeness scoring
- [ ] Cache hit rate monitoring
- [ ] Weak AI model performance metrics
- [ ] Function routing accuracy metrics
- [ ] Dashboards for latency SLOs and error rates
- [ ] Alerts for fallback spikes and model drift

### H. Performance & Reliability
- [ ] Model inference: ≤2ms (selector)
- [ ] Stage timeouts: Enrich 600ms (final validation)

### L. Advanced Features - Configuration Management
- [ ] Feature flags for per-intent rollouts
- [ ] A/B testing harness at policy layer
- [ ] Intent registry as data-driven configuration
- [ ] Environment-specific configuration management
- [ ] Function routing model configuration

### L. Advanced Features - Security & Privacy
- [ ] Tenant-level artifact retention policies (`retention_days` default 14)
- [ ] Artifact visibility controls (`private|org|public`)

### M. Documentation & API
- [ ] OpenAPI specification generation
- [ ] Interactive API documentation
- [ ] Schema documentation with examples
- [ ] Error code reference guide
- [ ] Function routing documentation
- [ ] Comprehensive README with setup instructions
- [ ] Troubleshooting guides and runbooks
- [ ] Function routing integration guide

### O. Enhanced Testing Requirements
- [ ] Named test fixtures covering all scenarios
- [ ] 100% golden test coverage with completeness_debt = 0
- [ ] Performance Testing (Load testing, Function routing latency, Cache performance, etc.)
- [ ] Integration Testing (Full docker-compose environment, ArangoDB integration, etc.)

## Success Criteria Validation

### Final Acceptance Checklist
- [ ] All golden tests pass with 100% coverage
- [ ] Performance requirements met under load testing
- [ ] Complete audit trail for 100% of requests
- [ ] Schema-agnostic functionality demonstrated
- [ ] Function routing accuracy >90% for natural language queries
- [ ] Fallback rate <5% under normal operations
- [ ] Evidence size management working correctly
- [ ] End-to-end Docker Compose deployment working
- [ ] Production readiness checklist completed
- [ ] New field support validated (tags, based_on, snippet, x-extra)
- [ ] Orphan data handling working correctly
- [ ] Cross-link reciprocity enforced for all relationship types

## Unmapped/Cross-Cutting Requirements

### Continuous Throughout All Milestones
- [ ] Vector index building/refreshing (ongoing optimization)
- [ ] BM25 text indexing (ongoing optimization)
- [ ] Cross-encoder for reranking ambiguous cases (optimization)
- [ ] Weak AI model performance metrics (ongoing monitoring)
- [ ] Seed data loading script (development support)
- [ ] Smoke test script (development support)
- [ ] Hot reload for development (development support)

### Notes on Coverage
- **100% Requirements Mapped**: All checklist items are assigned to specific milestones
- **Logical Sequencing**: Dependencies are respected (e.g., storage before caching, validation before streaming)
- **Milestone Focus**: Each milestone has a clear theme and deliverable set
- **Cross-cutting Concerns**: Some requirements (monitoring, documentation) span multiple milestones
- **Success Criteria**: Final validation ensures all requirements are met before completion