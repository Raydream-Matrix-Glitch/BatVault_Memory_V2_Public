# AI Knowledge System Development Roadmap

## Overview
This roadmap outlines the development phases for an AI-powered knowledge system featuring evidence gathering, validation, and intelligent query processing capabilities.

---

## M3 — Gateway Evidence + Validator + Weak AI (Baseline)

### In-Scope (MVP)
- **Evidence Gathering**
  - Gather all k=1 neighbors from Memory API (unbounded)
  - Preserve all evidence unless bundle exceeds `MAX_PROMPT_BYTES`
  - Implement deterministic truncation based on recency and similarity
  - Always maintain `MIN_EVIDENCE_ITEMS = 1`

- **Caching & Storage**
  - Evidence bundle cache with 15-minute TTL
  - Cache invalidation on `snapshot_etag` changes
  - Canonical prompt envelope with `prompt_fingerprint`
  - Store all artifacts for audit purposes

- **Validation & Metadata**
  - Include `prompt_id`, `policy_id`, `prompt_fingerprint`, `snapshot_etag` in metadata
  - Schema validation and ID scope checking
  - Mandatory citations enforcement
  - Retry mechanism (≤2 attempts) with templater fallback

- **Monitoring**
  - Log evidence bundling metrics
  - Persist all artifacts for audit trail

### Future Items / Extensions
- Learned evidence selector using GBDT/logistic regression
- Graph embeddings for related content discovery (Node2Vec, LightGCN)

---

## M4 — /v2/ask + /v2/query + LLM Integration

### Core Features
- **API Endpoints**
  - `/v2/ask`: Structured queries via Gateway pipeline
  - `/v2/query`: LLM function routing for natural language processing

- **LLM Functions**
  - `search_similar(query_text, k=3)`: Similarity-based content search
  - `get_graph_neighbors(node_id, k=3)`: Graph-based neighbor retrieval
  - Results merged into M3 bundler output

- **Processing**
  - LLM micro-summarizer in JSON-only mode
  - Temperature set to 0.0 for deterministic output
  - Retry mechanism (≤2 attempts) with templater fallback
  - Same validation rules as M3

- **Response Handling**
  - SSE streaming of validated `short_answer`
  - Comprehensive logging: `function_calls[]`, `routing_confidence`, `routing_model_id`
  - Artifact persistence including raw LLM JSON

### Future Enhancements
- Cross-encoder or advanced ML in routing
- Embedding-driven routing enhancements

---

## M5 — Frontend App + Audit Interface

### User Interface
- **Query Interface**
  - UI for `/v2/ask` and `/v2/query` endpoints
  - SSE streaming display for real-time results
  - Evidence cards with tags, snippets, based_on links, and orphan indicators

- **Schema Management**
  - Schema-agnostic field access via `/v2/schema/fields`
  - Field catalog browser
  - Dynamic field handling

- **Audit & Monitoring**
  - Audit Drawer with comprehensive request tracing
  - Prompt viewer and evidence inspector
  - Fingerprint tracking and stage timings
  - Cache indicators for performance monitoring

- **Data Visualization**
  - Relation graph visualization
  - Basic tag cloud functionality

- **Security & Reliability**
  - CORS allow-list configuration
  - Bearer/JWT authentication
  - Error boundaries and loading states

### Future Enhancements
- Advanced tag cloud analytics
- Enhanced decision graph analytics

---

## M6 — Harden + Golden Suites + Production Ready

### Quality Assurance
- **Testing**
  - Golden test cases from technical specification R4
  - Target: Coverage = 1.0, completeness_debt = 0
  - Schema-agnostic proof (new fields appear without code changes)

### Performance Requirements
- **Stage Timeouts**
  - Search: ≤ 800ms
  - Graph: ≤ 250ms
  - Enrich: ≤ 600ms
  - LLM: ≤ 1500ms
  - Validator: ≤ 300ms

- **API Performance**
  - `/v2/ask` p95 latency: ≤ 3.0s
  - `/v2/query` p95 latency: ≤ 4.5s
  - Fallback rate: < 5% under normal load

### Production Readiness
- **Reliability**
  - Load-shedding verification
  - 100% artifact retention per request
  - Comprehensive logging with `snapshot_etag` for ingest/API stages

- **Monitoring & Alerting**
  - Dashboards for latency, error rate, fallback spikes
  - Model drift detection and alerting
  - Performance metrics tracking

- **Documentation**
  - OpenAPI specification
  - Schema documentation
  - Deployment guide
  - Troubleshooting guide
  - Prompt envelope documentation

---

## Technical Architecture Notes

### Key Components
- **Memory API**: Core data storage and retrieval system
- **Gateway Pipeline**: Request processing and routing layer
- **Evidence Bundler**: Smart content aggregation and caching
- **LLM Integration**: Natural language processing and summarization
- **Validation Engine**: Schema and citation enforcement
- **Audit System**: Comprehensive request tracking and analysis

### Data Flow
1. Query reception via `/v2/ask` or `/v2/query`
2. Evidence gathering from Memory API
3. Content bundling and caching
4. LLM processing and summarization
5. Validation and citation checking
6. Response streaming via SSE
7. Artifact persistence for audit

This roadmap ensures a robust, scalable, and production-ready AI knowledge system with comprehensive monitoring, validation, and user interface capabilities.