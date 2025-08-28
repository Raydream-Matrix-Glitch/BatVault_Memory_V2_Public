# Project Development Milestones (Updated & Aligned)

## Milestone 0 — Bootstrapping & Health (Day 1-2)

**Goal:** `docker-compose up` → ArangoDB, Redis, MinIO + 4 Python services boot (FastAPI), health/ready endpoints, structured logging, deterministic IDs, repo layout scaffolding, CI baseline, smoke test.

### Key Deliverables

**Repository Structure:**
- Repo scaffold matching services, packages, operations, scripts, and memory data directories as per spec
- Configuration constants file with unified values across all services

**Services:**
- **API Edge Service**: Basic routes `/healthz`, `/readyz`, request idempotency keys (hash of body), SSE stub, CORS setup
- **Gateway Service**: Route shells, trace envelope, artifact sink to MinIO, **templater fallback implementation** (no LLM yet)
- **Memory API Service**: Stubs for `/api/enrich/*`, `/api/schema/*`, `/api/graph/expand_candidates`, `/api/resolve/text` returning fixture data
- **Ingest Service**: JSON loader + schema validation (ID regex: `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$`, timestamps, content fields) and field/relation catalog from memory data

**Core Packages:**
- **Core Libraries**: Logging (JSON with OTEL), IDs (deterministic fingerprinting), config, errors, models (Pydantic v2) with response contracts
- **Storage Adapter**: ArangoDB adapter stubs

**Operations:**
- **Container Configuration**: Dockerfiles per service, docker-compose (includes ArangoDB/Redis/MinIO), OTEL collector configuration
- **Environment variables**: Feature flags, cache TTLs, performance budgets

**Scripts:**
- **Utility Scripts**: Memory data seeding script (loads sample memory fixtures), smoke test script (pings health endpoints + basic flow)

**CI/CD:**
- GitHub Actions (lint, unit, docker build), pinned Python 3.11 / Node 20
- **Golden test framework** setup (empty tests that will be populated)

**Outcome:** You can run health checks, see structured logs & deterministic IDs end-to-end, and services communicate via HTTP. Templater returns deterministic answers.

---

## Milestone 1 — Ingest V2 + Catalogs + Core Storage (Day 3-5)

**Goal:** Turn sample JSON into real ArangoDB snapshot with `snapshot_etag`; enforce authoring rules; publish Field/Relation Catalog; idempotent upserts and cross-link repair.

### Key Deliverables

**Ingest Pipeline:**
- **Strict validation** + normalization per K-schemas (aliases, text normalization, UTC timestamps)
- **ID validation**: Enforce regex pattern `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$` (allows underscores)
- **New field support**: `tags`, `based_on`, `snippet`, `x-extra` processing with validation
- **Back-link derivation**: 
  - `event.led_to` ↔ `decision.supported_by`
  - `decision.based_on` ↔ `prior_decision.transitions`
  - Transitions appear in both decisions
- **Orphan handling**: Events without decisions, decisions without predecessors are valid

**ArangoDB Integration:**
- **Graph collections**: decisions, events, transitions with proper schemas
- **Vector indexes**: 768-dimensional HNSW indexes (preparation for embeddings)
- **AQL query foundations**: Basic traversal patterns for k=1 expansion
- **Upsert operations**: Idempotent node/edge creation

**Memory API (Real Implementation):**
- **Normalized envelopes**: `/api/enrich/{type}/{id}` returns clean, consistent data
- **Field/Relation catalogs**: `/api/schema/fields`, `/api/schema/rels` with live data
- **Snapshot ETag**: All responses include current corpus version in headers

**Testing:**
- **Contract tests** for orphan & empty-array cases in CI (coverage ≥1, completeness_debt = 0)
- **Cross-link validation** tests for bidirectional relationships including `based_on` links
- **New field validation** tests for tags, based_on, snippet, x-extra

**Outcome:** Real ArangoDB graph with proper schema, catalog endpoints working, cross-links enforced, orphan data handled gracefully.

---

## Milestone 2 — Memory API k=1 + Resolver + Caching (Day 6-8)

**Goal:** Real AQL for `/api/graph/expand_candidates` (k=1); BM25 resolver with optional vector flag; Redis caching; stage-level performance budgets.

### Key Deliverables

**Graph Operations:**
- **AQL k=1 traversal**: Real graph expansion with proper edge following
- **Performance budgets**: Search ≤800ms, Expand ≤250ms, Enrich ≤600ms (asyncio.wait_for enforcement)
- **Unbounded collection**: Collect all k=1 neighbors (truncation happens later in gateway)

**Resolver Pipeline:**
- **Slug short-circuit**: Known decision_ref → skip search entirely
- **BM25 text search**: Primary resolution method for decision lookup
- **Vector search**: Behind `ENABLE_EMBEDDINGS` feature flag
- **Confidence scoring**: All resolver methods return confidence scores

**Caching Layer (Redis):**
- **Resolver cache**: 5min TTL, key=normalized(decision_ref)
- **Expand cache**: 1min TTL, key=(decision_id, k, scope)  
- **Cache invalidation**: On snapshot_etag changes
- **Cache metrics**: Hit rates, performance impact tracking

**Performance Monitoring:**
- **OTEL spans**: All M2 stages instrumented
- **TTFB assertions**: CI enforces ≤600ms (slug) / ≤2.5s (search) p95
- **Stage timeout handling**: Graceful degradation on timeout

**Testing:**
- **Unit tests**: Resolver logic, cache behavior, AQL query generation
- **Contract tests**: k=1 expansion correctness, cache invalidation
- **Performance tests**: Stage-level timeout enforcement

**Outcome:** Fast, cached graph operations with proper resolution and performance budgets. ETag propagation working end-to-end.

---

## Milestone 3 — Gateway Evidence + Validator + Weak AI (Day 9-12)

**Goal:** Evidence bundling (unbounded collect, truncate only if `>MAX_PROMPT_BYTES` with selector), canonical prompt envelope + fingerprints, validator + deterministic fallback, weak AI baseline models.

### Key Deliverables

**Evidence Builder:**
- **Evidence size constants**:
  ```python
  MAX_PROMPT_BYTES = 8192  # Hard limit for bundle size
  SELECTOR_TRUNCATION_THRESHOLD = 6144  # Start truncating before hard limit
  MIN_EVIDENCE_ITEMS = 1  # Always keep at least anchor + 1 supporting item
  ```
- **Unbounded collection**: Gather all k=1 neighbors first
- **Size-based truncation**: Only truncate if bundle > MAX_PROMPT_BYTES
- **Selector model**: Deterministic baseline (recency + similarity scoring)
- **Evidence caching**: 15min TTL, invalidated on snapshot_etag change

**Weak AI Components:**
- **Bi-encoder resolver**: sentence-transformers model for decision similarity
- **BM25 fallback**: Always available lexical search
- **Evidence selector**: GBDT/logistic regression for truncation decisions
- **Feature extraction**: Text similarity, graph features, tag overlap, recency

**Prompt Management:**
- **Canonical Prompt Envelope**: Versioned, deterministic JSON structure
- **Fingerprinting**: SHA-256 of canonical envelope → `prompt_fingerprint`
- **Artifact retention**: Store envelope, rendered prompt, raw LLM, validator report, final response
- **MinIO integration**: Content-addressable storage by request_id

**Validation System:**
- **Schema validation**: Against WhyDecisionAnswer@1 and related schemas
- **ID scope checking**: `supporting_ids ⊆ allowed_ids` (strict)
- **Mandatory citations**: Anchor ID + present transition IDs must be cited
- **Deterministic fallback**: Templater when validation fails after retries

**Observability (Enhanced):**
- **Evidence metrics**: `total_neighbors_found`, `selector_truncation`, `dropped_evidence_ids`
- **Model metrics**: `resolver_confidence`, `selector_model_id`
- **Bundle metrics**: `bundle_size_bytes`, `final_evidence_count`
- **Complete artifact trail**: Every request fully auditable

**Testing:**
- **Golden tests**: Templater answers for Why/Who/When intents
- **Validation tests**: All edge cases (empty IDs, out-of-scope citations, etc.)
- **Selector tests**: Truncation behavior, feature scoring
- **Artifact tests**: Complete audit trail generation

**Outcome:** Evidence bundling with smart truncation, complete validation, weak AI baselines, full audit trails. Performance target: p95 ≤3s (/v2/ask slug).

---

## Milestone 4 — /v2/ask + /v2/query + LLM Integration (Day 13-16)

**Goal:** Full request path: NL routing → Memory API calls → evidence → LLM → validation → stream short answer via SSE; policy registry; intent-based structured queries.

### Key Deliverables

**API Endpoints (Full Implementation):**
- **POST /v2/ask**: Structured queries with explicit intent + decision_ref
- **POST /v2/query**: Natural language input with LLM function routing
- **Shared response schema**: WhyDecisionResponse@1 format for both endpoints

**Intent Router (New Component):**
- **Function routing**: LLM converts text → function calls:
  - `search_similar(query_text: string, k: int=3)` → Memory API `/api/resolve/text`
  - `get_graph_neighbors(node_id: string, k: int=3)` → Memory API `/api/graph/expand_candidates`
- **Memory API integration**: Route function calls to appropriate endpoints
- **Result merging**: Combine search + graph results into evidence bundle
- **Routing confidence**: Track and log how well NL → functions mapping worked

**LLM Integration:**
- **JSON-only mode**: Structured output generation with schema enforcement
- **Retry logic**: ≤2 retries with jittered backoff
- **Temperature=0**: Deterministic outputs for consistent results
- **Token budgets**: Respect MAX_PROMPT_BYTES limits
- **Fallback handling**: Auto-switch to templater on LLM failures

**Policy Management:**
- **Intent registry**: Policy configuration file with policy-as-data configuration
- **Policy versioning**: `policy_id` + `prompt_id` tracking
- **Rollout control**: Feature flags for policy A/B testing
- **Fallback semantics**: `fallback_used` flag in all responses

**Streaming Responses:**
- **SSE implementation**: Token-by-token streaming of `short_answer`
- **Buffer-then-stream**: Validate full response before streaming begins
- **Error handling**: Graceful fallback with user-friendly messages

**Load Shedding (Auto):**
- **Circuit breakers**: Auto-switch to `llm_mode=off` under load
- **Queue monitoring**: Track depth and automatically shed load
- **Performance budgets**: Enforce stage timeouts with graceful degradation
- **Load indicators**: `meta.load_shed=true` when triggered

**Testing:**
- **End-to-end tests**: Full NL query → streamed response flows
- **Policy tests**: Intent routing accuracy, fallback behavior
- **Load tests**: Circuit breaker triggering, performance under stress
- **Integration tests**: LLM + validation + streaming pipeline
- **Function routing tests**: Natural language → Memory API call mapping

**Outcome:** Complete question-answering pipeline with natural language support, intelligent load shedding, and streaming responses. Performance target: p95 ≤4.5s (/v2/query).

---

## Milestone 5 — Frontend App + Audit Interface (Day 17-19)

**Goal:** Next.js app with query UI, token streaming, comprehensive "Audit Drawer" showing complete request trace, tag/snippet display.

### Key Deliverables

**Core UI:**
- **Query interface**: Support both structured (/v2/ask) and natural language (/v2/query) inputs
- **Streaming display**: Real-time token rendering with proper SSE handling
- **Evidence visualization**: Cards showing events, decisions, transitions with new fields
- **Tag display**: Colored badges for categorization
- **Snippet integration**: Brief excerpts in evidence summaries

**Audit Interface (Comprehensive):**
- **Request trace**: Complete flow from query → evidence → LLM → response
- **Prompt viewer**: Expandable envelope → rendered prompt → raw LLM JSON
- **Evidence inspector**: Show truncation decisions, dropped items, selector scores
- **Fingerprint tracking**: Link related requests via prompt_fingerprint chains
- **Performance breakdown**: Stage timings, cache hits, model confidence scores
- **Function routing trace**: Show NL → function call mapping for /v2/query

**Advanced Features:**
- **Schema explorer**: Live browser for `/v2/schema/fields` and `/v2/schema/rels`
- **Decision graph**: Interactive visualization of decision/event/transition relationships
- **Tag cloud**: Aggregate view of all tags across the corpus
- **Completeness indicators**: Visual flags for partial/missing data
- **Based-on visualization**: Show decision dependency chains

**API Integration:**
- **CORS configuration**: Proper setup for frontend-backend communication
- **Auth integration**: Bearer/JWT token handling
- **Error boundaries**: Graceful handling of API failures
- **Loading states**: Proper UX during async operations

**Testing:**
- **E2E tests**: Full user workflows with Playwright/Cypress
- **Component tests**: React component behavior
- **API integration tests**: Frontend ↔ backend contract validation

**Outcome:** Professional frontend with complete audit capabilities, streaming responses, and rich data visualization. Users can trace every aspect of how answers are generated.

---

## Milestone 6 — Harden + Golden Suites + Production Ready (Day 20-22)

**Goal:** Comprehensive test suites, production monitoring, performance optimization, complete golden test coverage for all intents.

### Key Deliverables

**Golden Test Suites (Complete):**
- **Named golden test cases**:
  - `why_decision_panasonic_plasma.json` - Plasma TV exit with automotive pivot context
  - `why_decision_with_based_on.json` - Decision influenced by prior decisions  
  - `why_decision_tags_filtering.json` - Evidence filtering by tags
  - `event_with_snippet_display.json` - Snippet field in evidence bundle
  - `who_decided_anchor_v1.json` - Decision maker identification
  - `when_decided_anchor_v1.json` - Timeline reconstruction
  - `decision_no_transitions.json` - Empty array validation
  - `event_orphan.json` - No `led_to` validation
  - `decision_with_tags.json` - Tags array validation
  - `decision_based_on_validation.json` - Based_on link validation
- **Cross-link tests**: `based_on` relationship validation, bidirectional repair
- **Orphan handling tests**: Isolated events, decisions without predecessors
- **New field tests**: Tags, snippets, x-extra field handling

**Function Routing Test Suites:**
- **Router Contract Tests**:
  - `test_query_panasonic.py` - "Why did Panasonic exit plasma?" → Memory API calls + WhyDecisionResponse@1
  - `test_search_similar_routing.py` - Natural language → `search_similar()` calls
  - `test_graph_neighbors_routing.py` - Node queries → `get_graph_neighbors()` calls
  - `test_routing_confidence.py` - LLM function routing accuracy

**End-to-End Testing:**
- **Docker Compose e2e**: Full system testing in containerized environment  
- **Performance tests**: Load testing with realistic data volumes
- **Failure mode tests**: Network partitions, service failures, timeout scenarios
- **Cache performance tests**: Redis failure handling, TTL behavior

**Production Readiness:**
- **Monitoring dashboards**: Latency, error rates, cache hit ratios, model performance
- **Alerting**: SLO breach notifications, fallback spike detection
- **Error budgets**: Defined SLOs with automated monitoring
- **Security audit**: Auth flows, data handling, PII redaction

**Performance Optimization:**
- **Cache tuning**: Optimal TTLs based on real usage patterns
- **Database optimization**: ArangoDB index tuning, query optimization
- **Model optimization**: Weak AI model performance tuning
- **Resource optimization**: Memory usage, connection pooling

**Documentation:**
- **API documentation**: Complete OpenAPI specs with examples
- **Deployment guide**: Production deployment checklist
- **Troubleshooting guide**: Common issues and resolutions
- **Performance guide**: Optimization recommendations

**Quality Gates (Final):**
- **Test coverage**: Unit >90%, integration >80%, e2e >95%
- **Performance**: All SLOs met under realistic load
- **Reliability**: <5% fallback rate under normal operations
- **Security**: Complete security review passed
- **Documentation**: All APIs documented, deployment guide validated

**Outcome:** Production-ready system with comprehensive testing, monitoring, and documentation. All acceptance criteria met, performance targets achieved, golden tests at 100% coverage.

---

## Success Metrics (Updated)

### Performance Requirements
- **TTFB**: ≤600ms (known slug), ≤2.5s (search) 
- **Total latency**: p95 ≤3.0s (/v2/ask), p95 ≤4.5s (/v2/query)
- **Model inference**: ≤5ms (resolver), ≤2ms (selector)
- **Cache hit rates**: >80% (resolver), >60% (evidence), >40% (LLM)
- **Stage timeouts**: Search 800ms, Graph 250ms, Enrich 600ms, LLM 1500ms, Validator 300ms

### Quality Requirements  
- **Fallback rate**: <5% under normal load
- **Test coverage**: Golden tests 100%, unit >90%, integration >80%
- **Schema agnostic**: New JSON fields appear without code changes
- **Audit completeness**: 100% request traceability with artifacts
- **Function routing accuracy**: >90% correct NL → Memory API mapping

### Operational Requirements
- **Availability**: 99.9% uptime target
- **Error budget**: <0.1% 5xx errors
- **Recovery time**: <2min from service restart
- **Monitoring**: Complete observability with alerts on SLO breaches

### Evidence Management Requirements
- **Size constants**: MAX_PROMPT_BYTES=8192, SELECTOR_TRUNCATION_THRESHOLD=6144, MIN_EVIDENCE_ITEMS=1
- **Truncation logging**: 100% of truncation events logged with `selector_truncation=true`
- **ID validation**: 100% compliance with regex `^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$`