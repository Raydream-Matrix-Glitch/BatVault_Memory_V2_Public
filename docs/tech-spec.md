# V2 Batvault Memory - Implementation Requirements Checklist

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
- [ ] LLM function routing for natural language queries
- [ ] Evidence planner with schema-agnostic Graph Query Plan compilation
- [ ] Evidence bundle builder with k=1 neighbor collection
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
- [ ] Meta object: `{policy_id, prompt_id, retries, latency_ms, fallback_used}`
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
- [ ] `GET /api/enrich/decision/{id}` - normalized decision envelopes
- [ ] `GET /api/enrich/event/{id}` - normalized event envelopes
- [ ] `GET /api/enrich/transition/{id}` - normalized transition envelopes
- [ ] `POST /api/graph/expand_candidates` - k=1 neighborhood expansion
- [ ] `POST /api/resolve/text` - vector similarity search
- [ ] `GET /api/schema/fields` - field catalog endpoint
- [ ] `GET /api/schema/rels` - relation catalog endpoint

## D. Ingest Pipeline

### JSON Processing Pipeline
- [ ] File watcher with snapshot ETag generation
- [ ] JSON parsing with file/line diagnostics
- [ ] Strict validation (ID regex, enums, referential integrity)
- [ ] Artifact validation (ID, timestamp, content field requirements)
- [ ] Normalization/aliasing (schema-agnostic field mapping)
- [ ] Text processing (NFKC, trim, collapse whitespace, length limits)
- [ ] Timestamp parsing to ISO-8601 UTC
- [ ] Tag processing (lowercase, slugify, dedupe, sort)

### Data Derivation
- [ ] Backlink enforcement (`event.led_to ↔ decision.supported_by`)
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
- [ ] Evidence truncation constants:
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
- [ ] Snapshot ETag tracking in all logs

### Artifact Retention
- [ ] Query & resolver results storage
- [ ] Graph plan persistence
- [ ] Evidence bundle storage (pre/post limits)
- [ ] Prompt envelope archival
- [ ] Raw LLM JSON retention
- [ ] Validator report storage
- [ ] Final response JSON archival
- [ ] Artifact storage in MinIO/S3 with request_id keys

### Metrics & Monitoring
- [ ] TTFB and total latency tracking
- [ ] Retry and fallback usage metrics
- [ ] Coverage and completeness scoring
- [ ] Cache hit rate monitoring
- [ ] Weak AI model performance metrics
- [ ] Dashboards for latency SLOs and error rates
- [ ] Alerts for fallback spikes and model drift

## H. Performance & Reliability

### Performance Requirements
- [ ] `/v2/ask` p95 latency ≤3.0s for known slugs
- [ ] `/v2/query` p95 latency ≤4.0s for natural language
- [ ] TTFB ≤600ms (slug) / ≤2.5s (search)
- [ ] Stage timeouts: Search 800ms, Graph 250ms, Enrich 600ms, LLM 1500ms, Validator 300ms

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
- [ ] Golden tests for Why/Who/When intents with named fixtures
- [ ] Coverage = 1.0 and completeness_debt = 0 on fixtures
- [ ] Unit tests for all components
- [ ] Integration tests for service boundaries
- [ ] Contract tests for API compatibility
- [ ] End-to-end tests in Docker Compose environment

### Validation Gates
- [ ] Schema-agnostic proof: new JSON fields appear without code changes
- [ ] Function routing correctly maps natural language to Memory API calls
- [ ] All timestamps returned as ISO-8601 UTC
- [ ] Cross-link reciprocity enforcement
- [ ] Catalog endpoints reflect current JSON structure

## J. JSON Authoring Schemas

### Decision Schema
- [ ] ID validation (slug regex pattern)
- [ ] Required fields: id, option, rationale, timestamp, decision_maker
- [ ] Optional fields: tags, supported_by, based_on, transitions
- [ ] x-extra extensibility field

### Event Schema
- [ ] Required fields: id, summary, description, timestamp
- [ ] Optional fields: tags, led_to, snippet
- [ ] Summary repair logic when missing or equals ID
- [ ] x-extra extensibility field

### Transition Schema
- [ ] Required fields: id, from, to, relation, reason, timestamp
- [ ] Relation enum validation (causal, alternative, chain_next)
- [ ] Optional fields: tags
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
- [ ] Seed data loading script (`/scripts/seed_memory.sh`)
- [ ] Smoke test script (`/scripts/smoke.sh`)
- [ ] Hot reload for development

## L. Advanced Features

### Streaming & Real-time
- [ ] Server-sent events (SSE) for response streaming
- [ ] Progressive token rendering
- [ ] Frontend audit drawer with trace viewer
- [ ] Real-time completeness flag updates

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

## M. Documentation & API

### API Documentation
- [ ] OpenAPI specification generation
- [ ] Interactive API documentation
- [ ] Schema documentation with examples
- [ ] Error code reference guide

### Developer Experience
- [ ] Clear project structure with service boundaries
- [ ] Comprehensive README with setup instructions
- [ ] API client examples and SDKs
- [ ] Troubleshooting guides and runbooks

## L. Front-end & Streaming Integration
- [ ] Build static assets into `/public` (Next.js `out/` or Vite `dist/`).
- [ ] Serve them from the Edge service at `/` via a catch-all route.
- [ ] Use native `EventSource('/v2/ask?...')` in the browser to subscribe to SSE.
- [ ] Provide a React hook or equivalent for token-by-token rendering:
      ```ts
      function useSSE(url: string, onMessage: (msg: Token) => void) {
        useEffect(() => {
          const es = new EventSource(url);
          es.onmessage = e => onMessage(JSON.parse(e.data));
          return () => es.close();
        }, [url]);
      }
      ```
- [ ] Lazy-load the graph component (D3/vx) and on node-click call  
      `/api/graph/expand_candidates` → merge neighbors into the graph.

## M. LLM Deployment & Hosting
- [ ] **Model**: Mistral-7B-Instruct quantized to 8-bit (fits ~6–7 GB on RTX 4080).  
- [ ] **Serving**: Hugging Face Text-Generation-Inference Docker image with GPU (CUDA) support.  
- [ ] **Compose** snippet example:
    ```yaml
    llm:
      image: ghcr.io/huggingface/text-generation-inference:latest
      runtime: nvidia
      environment:
        - MODEL_ID=mistralai/Mistral-7B-Instruct
        - QUANTIZATION=8bit
      ports:
        - "8080:8080"
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
    ```
- [ ] **Endpoint**: `/v1/models/Mistral-7B-Instruct:predict` with streaming enabled.  
- [ ] **Feature-flag**: `ENABLE_EMBEDDINGS=true` (for vector search, not for LLM).  
- [ ] **Fallback**: when `llm_mode=off` or LLM fails, use templater.

## N. MicroK8s vs Docker-Compose POC
- [ ] **Infrastructure**:  
    - Edge & Gateway services → deploy on MicroK8s  
    - LLM service → deploy via Docker-Compose on WSL2 (GPU passthrough)  
- [ ] **Networking**: ensure MicroK8s pods can reach `localhost:8080` (LLM).  
- [ ] **Deployment script**: update `docker-compose.yml` to include `llm` service with `runtime: nvidia`.

## Success Criteria

### Final Acceptance
- [ ] All golden tests pass with 100% coverage
- [ ] Performance requirements met under load testing
- [ ] Complete audit trail for 100% of requests
- [ ] Schema-agnostic functionality demonstrated
- [ ] Fallback rate <5% under normal operations
- [ ] End-to-end Docker Compose deployment working
- [ ] Production readiness checklist completed

### Additional Information (appended) - IMPORTANT

## F. Front-end Integration

- **Static build**: Produce a `/public` bundle (e.g. Next.js `out/` or Vite `dist/`).  
- **Serving**: Edge service hosts `/index.html` and assets at `/` via a catch-all route.  
- **Streaming**: Front-end subscribes to SSE on `/v2/ask?…` using native `EventSource`.  
- **Rendering**: Client renders each JSON‐token chunk as it arrives (plain text, code blocks, etc.).  
- **Graph UI**: Lazy-load D3/vx component; on node click call `/api/graph/expand_candidates` and merge neighbors.

## G. LLM Deployment & Hosting

- **Model**: Mistral-7B-Instruct (8-bit quantized) for ~6–7 GB VRAM footprint on RTX 4080.  
- **Server**: Use HuggingFace Text-Generation-Inference Docker image with GPU mode.  
- **Compose snippet**:
  ```yaml
  llm:
    image: ghcr.io/huggingface/text-generation-inference:latest
    runtime: nvidia
    environment:
      - MODEL_ID=mistralai/Mistral-7B-Instruct
      - QUANTIZATION=8bit
    ports:
      - "8080:8080"
  ```
- **Endpoint**: `/v1/models/Mistral-7B-Instruct:predict` (streaming).  
- **Fallback**: When `llm_mode=off` or on failure, gateway uses the templater service.

## H. Deployment POC: MicroK8s + Docker-Compose

- **Infra split**:  
  - Edge & Gateway run on MicroK8s.  
  - LLM service runs via Docker-Compose on WSL2 with `runtime: nvidia`.  
- **Networking**: Ensure MicroK8s pods can reach the LLM endpoint at `localhost:8080`.  
- **Compose**: Include `llm` service alongside existing `arangodb`, `redis`, etc., so `docker-compose up` brings up the full POC stack.