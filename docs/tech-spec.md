# V2 Batvault Memory - Technical Specification

## A. Scope & North Star

**Vision**: One endpoint, intent-first, JSON-only, FE formats UI

- **Single Endpoint**: `POST /v2/ask` with intent (e.g., `why_decision`, `who_decided`, `when_decided`, `chains`) + options
- **Natural Language Gateway**: `POST /v2/query` with LLM function routing for text queries
- **Planner-lite** builds a schema-agnostic Graph Query Plan (k=1 for "why")
- **Evidence Bundle** (deterministic, cacheable) → micro-LLM (JSON-only) or deterministic templater
- **Blocking validation** + deterministic fallback
- **Structured logging** across all stages; artifact retention for audit & learning

## A1. Components & Boundaries (Normative Responsibilities)

| Component | Responsibilities |
|-----------|------------------|
| **API Edge** | HTTP, auth, rate-limit, idempotency, streams rendered tokens only |
| **Gateway** | resolve → plan → exec graph → build bundle → prompt → LLM → validate → render → stream; writes artifacts |
| **Intent Router** | LLM function routing for natural language queries; calls Memory API |
| **Storage/Catalog** | JSON watcher → field/relation catalogs + ETag snapshot; write to ArangoDB (graph+vector) |

## A1.1. Weak AI Philosophy

**LLM does one thing only:** produce the `answer.short_answer` JSON (and only when `llm_mode!=off`).

**Everything else is "weak AI"** (small, cheap models) or deterministic logic:
* Keeps p95 latency by avoiding heavy calls for search, ranking, or validation
* Gives you clear fallbacks: if a weak model is missing or low‑confidence, we fall back to the **deterministic templater + rule pipeline**

## A2. Project Structure

```
/services
  /api-edge
    /routes
    /middleware (auth, rate-limit, idempotency)
    /adapters (gateway client)
    /schemas (request/response envelopes from core-models)
    /tests/{unit,integration}
  /gateway
    /intents/registry.json            # policy-as-data; prompt_id, graph_scope (JSON format, not watched by snapshot)
    /planner/                         # resolve → plan → exec (SA-GQL compiler)
    /resolver/                        # weak AI models for anchor resolution
      embedding_model.py              # bi-encoder for decision search
      reranker.py                     # cross-encoder for ambiguous cases
      fallback_search.py              # BM25 lexical search
    /evidence/                        # bundle builder (k=1 for why), cache
      selector_model.py               # learned scorer for event/transition selection
    /embeddings/                      # graph representation learning
      graph_vectors.py                # Node2Vec/LightGCN embeddings
    /prompt/                          # system templates, JSON-only user msg composer
    /trace/                           # Trace & Envelope builders, artifact sink
    /llm/                             # client, retries, JSON validation
    /validator/                       # schema + id-scope + mandatory IDs
    /renderer/                        # short_answer → streamed text
    /intent_router/                   # LLM function routing for /v2/query
    /endpoints/                       # query.py, ask.py
    /telemetry/                       # service-specific meters and spans
    /tests/{unit,integration,golden}
  /ingest
    /schemas/json-v2/*.json           # authoring schemas for decisions/events/transitions
    /pipeline/                        # parse, validate, alias, derive, index
      graph_upsert.py                 # ArangoDB graph operations
      vector_upsert.py                # ArangoDB vector operations
    /catalog/                         # fields/relations
    /api/                             # normalize/enrich/expand, schema endpoints
    /tests/{unit,contract}
  /memory-api
    /tests/{unit,integration,contract}
/packages
  /core-logging
  /core-telemetry
  /core-config
  /core-errors
  /core-ids
  /core-models                        # Pydantic/OpenAPI for wire contracts only
    /schemas/                         # JSON schemas (single source of truth)
  /core-storage                       # ArangoDB adapters
    graph_store.py                    # Graph operations
    vector_store.py                   # Vector search operations
/apps
  /frontend
    /src
    /public
    /e2e
    Dockerfile
/ops
  /docker/                           # Dockerfiles per service
  /compose/docker-compose.yml        # includes ArangoDB service
  /otel/collector.yaml
  /minio/                            # artifact store bootstrapping
/scripts
  /seed_memory.sh                    # loads /memory into ingest
  /smoke.sh                          # end-to-end check in compose
/docs
  /openapi/                          # generated OpenAPI for API edge + memory API
/tests
  /golden/why_pause_paas_v1.json
  /golden/who_decided_anchor_v1.json
  /e2e/compose/                      # black-box API tests
/memory
  /decisions/*.json
  /events/*.json
  /transitions/*.json
```

## B. Deliverables (MVP → Ready for Growth)

### B1. API & Contracts

**Core Endpoints**:
- `/v2/ask` (structured endpoint with explicit intent)
- `/v2/query` (natural language endpoint with LLM function routing)
- Shared JSON models (Pydantic/OpenAPI)

**Query Endpoint Contract**:
```json
POST /v2/query
{
  "text": "Why was the PaaS rollout paused?"
}

Response:
{
  "intent": "why_decision",
  "evidence": { /* Evidence Bundle */ },
  "answer": { /* Answer object */ },
  "completeness_flags": { /* flags */ },
  "meta": {
    "function_calls": ["search_similar", "get_graph_neighbors"],
    "routing_confidence": 0.85,
    "policy_id": "query_v1",
    "prompt_id": "router_v1",
    "retries": 0,
    "latency_ms": 1250
  }
}
```

**LLM Function Routing**:
- `search_similar(query_text: string, k: int=3)` → Memory API `/api/resolve/text` or ArangoDB vector search
- `get_graph_neighbors(node_id: string, k: int=3)` → Memory API `/api/graph/expand_candidates` or AQL traversal

**Data Models**:
- **Evidence**: `{anchor, events[], transitions{preceding|succeeding}, allowed_ids[]}`
- **Answer**: `{short_answer, supporting_ids[]}`
- **Response**: `{intent, evidence, answer, completeness_flags, meta{policy_id, prompt_id, retries, latency_ms}}`
- **Intent Registry**: Data-driven; add intents via config, not code

### B2. Evidence Planner (Schema-Agnostic)

Components:

Anchor Resolver: Precedence rule - if decision_ref is known slug → skip search; else run exactly one cheap decision search
Graph Scope: Per intent (k, directions, relationships, unbounded collection)
Field/Relation Catalog: Auto-derived from JSON docs

get_field(node, "rationale|owner|decider|timestamp|reason|phase|...")
Relations: LED_TO, CAUSAL_PRECEDES (in/out), CHAIN_NEXT (for "chains")


Compilation: To SA-GQL (compiles to AQL for k=1 traversals; vector search uses ArangoDB vector API)
Bundle Cache: Per decision (short TTL; invalidated on snapshot ETag change)

Evidence Collection (for why_decision):

k=1 neighbors; collect all events and transitions
allowed_ids = exact union of {anchor.id} ∪ events[].id ∪ present transition ids (post-truncation if applied)
Soft Limits: If json.dumps(bundle).length > MAX_PROMPT_BYTES, selector truncates lowest-scoring items
Logging: selector_truncation=true when evidence is dropped for size/latency budgets

Section B2.1 - Weak AI Components (Gateway Services) - UPDATE SECTION C
C) Event/Transition Selector v0 (learned scorer) - Updated

Where: Gateway: Evidence Bundle builder, when bundle size exceeds prompt budget
What: A small GBDT / logistic regression or tiny MLP that scores all k=1 neighbors; features:

Text similarity (cosine between event/transition text and anchor rationale/question)
Graph features (in/out degree, recency delta)
Tags overlap (finance/risk/etc.)
Historical click/feedback priors (later)


Behavior: Collect all neighbors first, then truncate only if bundle size > MAX_PROMPT_BYTES
Fallback: Simple deterministic sort (recency + similarity)
Why: Preserves all relevant evidence unless prompt size forces truncation
Logs: selector_model_id, selector_truncation, feature snapshot for dropped items (auditable)
Location: /services/gateway/evidence/selector_model.py

### B3. Answerers (Pluggable Strategies)

**Strategy Options**:
- **Templater**: Deterministic fallback; compose `short_answer` from evidence
- **LLM Micro-summarizer**: JSON-only; temp=0; max_tokens small; `allowed_ids` enforced
- **Auto Policy**: Try LLM → on failure retry ≤2 → fallback to templater

### B4. Validator (Blocking)

**Validation Rules** (testable):
- **Schema Check**: Validate against `WhyDecisionAnswer@1`
- **ID Scope**: `supporting_ids ⊆ allowed_ids`
- **Mandatory IDs**: `anchor.id` must be cited; any present preceding/succeeding transition IDs must be cited
- **Failure Actions**: Deterministic retries → fallback templater → never stream junk

**Artifact-Level Validation (Ingest)**:

| Field | Rule | Rationale |
|-------|------|-----------|
| `id` | Non-empty, slug-regex (`^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$`) | Unique lookup key |
| `timestamp` | ISO-8601 UTC (`Z`) | Enables correct ordering & recency logic |
| `rationale / description / reason / summary` | ≥1 non-whitespace char after trim |Prevents useless stubs that defeat downstream similarity checks |

Everything else (tags, x-extra, links) stays **extensible** and validated only if present.

### B5. Observability & Audit

**Structured Spans (OpenTelemetry)**: 
`resolve → plan → exec → enrich → bundle → prompt → llm → validate → render → stream`

**Deterministic IDs** (always include in meta and span attributes):
- `request_id`, `prompt_fingerprint`, `plan_fingerprint`, `bundle_fingerprint`
- `snapshot_etag`, `prompt_id`, `policy_id`
- `resolver_model_id`, `selector_model_id` (when weak AI models used)

**Per-Request Artifacts** (persisted with `request_id`):
- Query & resolver result (including weak AI model scores)
- Graph Plan
- Evidence Bundle (pre/post limits, with selector feature snapshots)
- Prompt Envelope (see Section J)
- Raw LLM JSON
- Validator report
- Final Response JSON

**Artifacts**: Written to s3://{bucket}/{request_id}/... (or MinIO) and referenced in the Replay endpoint.

**Generic Log Envelope**:
```json
{
  "timestamp": "2024-07-28T14:30:00Z",
  "level": "INFO",
  "service": "gateway",
  "request_id": "req_...",
  "snapshot_etag": "sha256:abc123...",
  "stage": "resolve|plan|exec|bundle|prompt|llm|validate|render",
  "latency_ms": 123,
  "meta": {
    // Stage-specific fields: resolver_confidence, selector_features, etc.
    // NEW FIELDS FOR EVIDENCE BUNDLING:
    "total_neighbors_found": 12,
    "selector_truncation": true,
    "final_evidence_count": 8,
    "dropped_evidence_ids": ["low-score-event-1"],
    "bundle_size_bytes": 7680,
    "max_prompt_bytes": 8192
  }
}
```

**Metrics**:
- TTFB, total latency, retries, fallback_used
- Coverage, completeness, cache hit rate
- Weak AI model performance: resolver_confidence, selector_accuracy, reranker_precision
- Dashboards + alerts on latency SLOs, error rate, fallback spikes, model drift

## C. The Auditable Prompt Builder

**Goal**: Every token that hits the LLM is reconstructible, signed, and explainable.

### 1. Prompt Envelope (Canonical JSON Object, Versioned)

```json
{
  "prompt_version": "why_v1",
  "intent": "why_decision",
  "policy": {"json_mode": true, "retries": 2, "temperature": 0.0},
  "question": "Why was the PaaS rollout paused?",
  "evidence": { /* Evidence Bundle (minified) */ },
  "allowed_ids": ["pause-...","B-E1","B-E2","B-E3","trans-...","trans-..."],
  "constraints": {
    "output_schema": "WhyDecisionAnswer@1",
    "max_tokens": 256,
    "forbidden_text": ["```","<xml>"]
  },
  "explanations": {
    "why_these_fields": "k=1 neighbors only; minimal fields required for causality",
    "why_these_ids": "anchor, direct events, in/out transitions"
  }
}
```

### 2. Canonicalization & Fingerprinting

- **Deterministic Processing**: Key ordering + normalized whitespace → `canonical_json`
- **Fingerprinting**: SHA-256 over `canonical_json` → `prompt_fingerprint`
- **Snapshot Strategy**: Combine content hash + timestamp into `snapshot_etag` to avoid collision edge-cases
- **Storage**: Store both Envelope and fingerprint; include fingerprint in all logs

### 3. Rendering

**LLM Input Structure**:
- **System**: "JSON-only; emit Answer schema; use only allowed_ids."
- **User**: The Prompt Envelope (or minimal required sub-object)
- **Persistence**: Store Rendered Prompt (exact string/bytes sent) alongside Envelope

### 4. Audit UX

**Trace Viewer Flow**: 
`Envelope → rendered prompt → raw LLM JSON → validation report → final response`

**Explainability**: "Explain" button derives why each token/ID is present from Envelope's explanations and Plan

### 5. Safety & Privacy

- **Redaction**: Best-effort in Envelope ("PII" fields list; reversible hash salts per request if needed)
- **Security**: No secrets in prompt; secrets only in transport layer config

## D. Easy Adds That Pay Off

**Performance & Reliability**:
- **Idempotency**: Keys + request hash → dedupe concurrent identical calls
- **Storage**: Content-addressable storage for artifacts (fingerprint-named objects)
- **Caching**: TTL caching at three layers: resolver, bundle, LLM JSON (for hot anchors)

**Development & Testing**:
- **Feature Flags**: Per-intent prompt/policy rollouts (why_v1, why_v2)
- **Golden Tests**: Frozen Evidence Bundles → expected Answers for CI gatekeeping
- **A/B Testing**: Harness at policy layer (templater vs LLM vs learned)

**Quality & Performance**:
- **Latency Budgeting**: Token-budget table before LLM step; auto-shrink evidence if over budget
- **Quality Guard**: Compare LLM answer to templated baseline; flag big divergences
- **Explainability Flags**: `{"missing_preceding": true, "missing_succeeding": false, "event_count": 1}`

**Evaluation**:
- **Offline Eval**: Export (Envelope, LLM JSON, Validator report) for batch scoring

## E. Future-Ready (No Heuristics, Just Intelligence)

**ML Evolution Path**:
- Swap Resolver and Selector for learned models (same inputs, same outputs)
- Train summarizer on Envelope→Answer pairs; API and contracts stay unchanged
- Add chains via virtual view (walk `CAUSAL_PRECEDES`) or materialized `CHAIN_NEXT` edge

## F. Explicit Contracts & Schemas

### F1. JSON Schemas (Normative, Versioned)

**WhyDecisionEvidence@1**:
- `anchor`: `{id, title?, rationale?, timestamp?, decision_maker?}`
- `events[]`: `{id, summary?, timestamp?, led_to? [anchor.id]}` (unbounded*)
- `transitions.preceding?/succeeding?`: `{id, from, to, reason?, timestamp?}` (unbounded*)
- `allowed_ids[]`: Must equal `{anchor.id} ∪ events[].id ∪ present transitions' ids`
- **Constraints**: All IDs are non-empty strings

**WhyDecisionAnswer@1**:
- `short_answer`: ≤320 chars
- `supporting_ids[]`: minItems≥1, items⊆allowed_ids
- `rationale_note?`: ≤280 chars

**WhyDecisionResponse@1**:
- `{intent, evidence, answer, completeness_flags, meta}`
- `completeness_flags`: `{has_preceding: bool, has_succeeding: bool, event_count: int}`
- `meta`: `{policy_id, prompt_id, retries, latency_ms}` (all required)

### F2. Error Envelope (Consistent Across Intents)

```json
{
  "error": {
    "code": "LLM_JSON_INVALID",
    "message": "...",
    "details": {"reasons": ["schema:...","unsupported_ids:..."]},
    "request_id": "..."
  }
}
```

**Error Codes**: `ANCHOR_NOT_FOUND`, `TIMEOUT`, `LLM_JSON_INVALID`, `VALIDATION_FAILED`, `RATE_LIMITED`

## G. Streaming & Fallback Semantics

**JSON Mode Behavior**:
- Buffer model output → validate → stream rendered `short_answer` (tokenized)
- If validation fails after ≤2 retries → emit templated answer
- Set `meta.fallback_used=true` & `error=null`

**Deterministic Fallback Triggers**:
1. JSON parse error
2. Schema error  
3. `supporting_ids ⊄ allowed_ids`
4. Missing mandatory IDs (anchor + present transitions)

## H. Performance & Timeouts

### H1. Performance Budgets (Request-Level)
- **TTFB**: ≤600ms (slug) / ≤3.0s (search)
- **p95 Total**:  ≤4.0s - increased to accommodate unbounded evidenc

### H2. Stage Timeouts
- Search: 800ms
- Graph expand: 250ms
- Enrich: 600ms
- LLM: 1500ms (including retries)
- Validator: 300ms

### H3. Retry & Cache Policy

**Retries**:
- **LLM JSON retries**: ≤2
- **HTTP calls**: Single retry with jittered backoff (cap 300ms)

**Cache Keys & TTLs**:
- **Resolver**: `key = normalize(decision_ref)`, TTL 5min
- **Evidence Bundle**: `key = (decision_id, intent, graph_scope, snapshot_etag, truncation_applied)`, TTL 15min, invalidate on snapshot ETag change
- **LLM JSON**: `key = (intent, decision_id, question, bundle_fingerprint, prompt_id)`, TTL 2min (hot anchors)

## I. Schema-Agnostic Implementation

### I1. Field Catalog Contract
- **Endpoints**: 
  - Memory API serves `/api/schema/*` (authoritative)
  - Gateway mirrors at `/v2/schema/*` (read-through cache)
  - API edge proxies requests
- **Purpose**: Publish stable semantic names and current JSON aliases
- **Example**: `rationale → ["rationale","why","reasoning"]`
- **Schema-Agnostic Proof**: Adding new JSON field appears with zero code changes

### I2. SA-GQL Shape
json
{
  "from": "decision:<id>",
  "out": [
    {"rel": "LED_TO", "type": "event"},
    {"rel": "CAUSAL_PRECEDES", "dir": "in", "type": "transition"},
    {"rel": "CAUSAL_PRECEDES", "dir": "out", "type": "transition"}
  ],
  "prompt_budget": {
    "max_prompt_bytes": 8192,
    "selector_truncation_enabled": true
  }
}
```

**Execution**: Compiles to AQL for k=1 traversals; vector search uses ArangoDB vector API. No limit fields - caller may set max for performance, but default is unbounded collection.

## J. Ingest V2 (JSON authoring & service)

**Goal:** Authors write concise JSON; ingest guarantees correctness, derives cross-links, and publishes a stable graph plus a **Field/Relation Catalog**. The gateway remains schema‑agnostic.

### J1. Pipeline (with structured logs per stage)
1) **Watch & snapshot** → compute `snapshot_etag` for this batch; include in all downstream logs/artifacts
2) **Parse** → JSON → objects; track file/line for diagnostics
3) **Validate** (STRICT mode; fail closed):
   - ID regex: `^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$`, global uniqueness
   - Enums: `relation ∈ {causal, alternative, chain_next}`
   - Referential integrity: if array **exists and is non‑empty**, each ID must resolve; empty array or missing field is allowed
   - **Artifact validation**: ID, timestamp, content field requirements per table above
4) **Normalize/alias** (schema‑agnostic support):
   - Map synonyms into core names (e.g., `title→option`, `why|reasoning→rationale`); keep originals in `x-extra`
   - Text: NFKC, trim, collapse whitespace; bounded lengths (rationale ≤600 chars; reason ≤280; summaries ≤120)
   - Timestamps: parse common formats → ISO‑8601 UTC (`Z`)
   - Tags: lowercase slugify; dedupe; sort
5) **Derive**:
   - Backlinks: ensure `event.led_to ↔ decision.supported_by`; ensure `transition.{from,to}` appears in both decisions' `transitions[]`
   - **Field Catalog**: publish semantic names → detected aliases
   - **Relation Catalog**: publish available edge types (`LED_TO`, `CAUSAL_PRECEDES` in/out, `CHAIN_NEXT`)
6) **Persist**:
   - **ArangoDB upserts**: nodes/edges to Arango graph; build/refresh vector indexes
   - Content‑addressable snapshot keyed by `snapshot_etag`
   - Indexes: text (BM25), vector embeddings (ArangoDB), adjacency lists (AQL)
7) **Serve** (Memory API v2):
   - `/api/graph/expand_candidates` (AQL traversal for k=1)
   - `/api/enrich/{type}/{id}` → normalized envelopes (Decision/Event/Transition)
   - `/api/schema/fields`, `/api/schema/rels` → catalogs
   - `/api/resolve/text` → ArangoDB vector search

**Logging per batch:** `{snapshot_etag, files_seen, nodes_loaded, edges_loaded, warnings, errors, timings}`

## K. JSON V2 Authoring Schemas (normative)

> Minimal, predictable authoring; ingest handles aliasing/derivations. Authors may include `x-extra` for future fields.

### K1. Decision
```json
{
  "id": "pause-paas-rollout-2024-q3",
  "option": "Pause PaaS rollout",
  "rationale": "Q2 financials revealed cashflow guard-rails were breached...",
  "timestamp": "2024-07-20T14:30:00Z",
  "decision_maker": "Bob",
  "tags": ["finance", "risk_mitigation"],
  "supported_by": ["B-E1", "B-E2", "B-E3"],
  "based_on": ["enter-cloud-market-2024-q1"],
  "transitions": [
    "trans-2024-08-cashflow-risk-triggered",
    "trans-2025-01-pause-to-partnership"
  ],
  "x-extra": {}
}
```

### K2. Event
```json
{
  "id": "B-E1",
  "summary": "Q2 report shows 40% infra overspend",
  "description": "Q2 financial report shows a 40% overspend in infrastructure costs.",
  "timestamp": "2024-07-19T08:00:00Z",
  "tags": ["financial_report", "cost_spike"],
  "led_to": ["pause-paas-rollout-2024-q3"],
  "snippet": "Q2 financial report shows a 40% overspend...",
  "x-extra": {}
}
```

### K3. Transition
```json
{
  "id": "trans-2024-08-cashflow-risk-triggered",
  "from": "enter-cloud-market-2024-q1",
  "to": "pause-paas-rollout-2024-q3",
  "relation": "causal",
  "reason": "Guard-rail breached: forecast cash-on-hand fell below threshold…",
  "timestamp": "2024-08-12T09:05:00Z",
  "tags": ["finance", "risk_assessment"],
  "x-extra": {}
}
```

## L. Memory API v2 (Normalization & Catalog)

**Principle:** The API always returns **normalized, small envelopes**. Gateway composes Evidence Bundles from these.

### L1. Endpoints
- `GET /api/enrich/decision/{id}` →  
  `{id, option, rationale, timestamp, decision_maker?, tags[], supported_by[], based_on[], transitions[]}`
- `GET /api/enrich/event/{id}` →  
  `{id, summary, description?, timestamp, tags[], led_to[], snippet?}`
- `GET /api/enrich/transition/{id}` →  
  `{id, from, to, relation, reason, timestamp, tags[]}`
- `POST /api/graph/expand_candidates` → k=1 neighborhood using AQL traversal
- `POST /api/resolve/text` → ArangoDB vector search
- `GET /api/schema/fields` → Field Catalog (semantic name → aliases)
- `GET /api/schema/rels` → Relation Catalog (edge types available)

**Implementation notes**: 
- `/api/graph/expand_candidates` executes AQL
- `/api/enrich/*` reads envelopes from ArangoDB
- `/api/resolve/text` queries ArangoDB's vector API

### L2. Normalization rules (applied by API)
**IDs:** NFKC → lowercase → trim → spaces/punct → `-` → collapse; regex `^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$`
**Text:** trim, collapse internal whitespace; rationale ≤600, reason ≤280, summary ≤120; preserve punctuation
**Timestamps:** parse input → ISO‑8601 UTC (`Z`); include `x-extra.source_tz` if converted
**Tags:** lowercase slugs; dedupe; sort
**Aliases:** `title→option`, `why|reasoning→rationale`, etc., via Field Catalog
**Cross‑links:** enforce `event.led_to ↔ decision.supported_by`; ensure transitions appear in both `from/to` decisions' `transitions[]`
**Event summary repair:** if `summary` missing or equals the ID, set `summary = clipped(description, 96)`; derive `snippet` from first sentence if missing

### L3. Structured logging (per call)
Log `{snapshot_etag, node_id, before_aliases, after_normalization, derived_links[], warnings[], errors[]}`, plus timings.

## M. Technology Stack & Performance

### M1. Technology Stack
- **Python 3.11** (FastAPI/Uvicorn) for api-edge, gateway, memory-api, ingest
- **Node 20** (Next.js/React) for frontend
- **ArangoDB Community** (graph+vector store)
- **Redis 7** for caches
- **MinIO** for artifact store (optional but makes artifacts real)

### M2. Performance Requirements
1. `/v2/ask` with intent=why_decision&decision_ref=<slug> returns in ≤3.0s p95 with fallback_used=false (increased for larger evidence)
2. `/v2/query` with natural language returns in ≤4.5s p95 (increased for larger evidence)

### M3. Reliability Requirements  
3. If LLM returns invalid JSON or out-of-scope IDs, endpoint still returns valid response with fallback_used=true (no user-visible error)
3.1 Evidence Truncation: When bundle size exceeds MAX_PROMPT_BYTES, selector logs selector_truncation=true and preserves highest-scoring evidence

#### M3.1

M4. Evidence Size Management
Configuration Constants:
python# Evidence size management
MAX_PROMPT_BYTES = 8192  # ~4k tokens with metadata
SELECTOR_TRUNCATION_THRESHOLD = 6144  # Start truncating before hard limit
MIN_EVIDENCE_ITEMS = 1  # Always keep at least anchor + 1 supporting item
Truncation Behavior:

Planner expands k=1 and collects all neighbors
If json.dumps(bundle).length > MAX_PROMPT_BYTES, selector down-ranks and drops lowest-score items until size fits
Log selector_truncation=true to track evidence loss
allowed_ids reflects final post-truncation evidence set

### M4. Audit Requirements
4. **Every response** includes `{prompt_id, policy_id, prompt_fingerprint}` and **artifacts** are persisted (Envelope, rendered prompt, raw LLM JSON, validator report, final response)

### M5. Schema-Agnostic Proof
5. Adding new JSON field (e.g., `phase_label`) with **zero code changes**; field appears in `/v2/schema/fields`

### M6. Quality Gates
6. **Golden tests** pass for Why/Who/When with **named fixtures** (e.g., `why_pause_paas_v1.json`, `who_decided_anchor_v1.json`, `when_decided_anchor_v1.json`); **coverage = 1.0** and **completeness_debt = 0** on fixtures

## N. Load-Shedding & Circuit Breakers

**Auto Load-Shedding**:
- If queues or time budgets breach thresholds → automatically set `llm_mode=off` (templater)
- Skip search when slug is present
- Return `meta.load_shed=true`

**Purpose**: Preserves availability and p95 under stress while keeping contract stable

## O. Artifact Retention & Access

**Governance**:
- **Tenant-level** `retention_days` (default 14)
- **Artifact visibility**: `private|org|public`
- **Artifacts**: Envelope, rendered prompt, raw LLM JSON, validator report, final response

**Purpose**: Makes audit trail usable in practice and compliant with different data policies

## P. Additional Acceptance Criteria (Ingest & Memory API)

1) Ingest produces a **publishable snapshot** with `snapshot_etag`; Memory API responses include this ETag in headers and logs
2) All **timestamps** in enrich responses are ISO‑8601 UTC (`Z`)
3) **Event summary repair**: any event with `summary` empty or equal to its ID is returned with `summary` derived from `description` (and `snippet` present)
4) **Cross‑link reciprocity**: `event.led_to` decisions include the event in `supported_by`; transitions appear in both related decisions' `transitions[]`
5) **Catalog endpoints** (`/api/schema/fields`, `/api/schema/rels`) are live and reflect current JSON; gateway mirrors them under `/v2/schema/*`
6) **k=1 expansion** collects all neighbors; gateway may truncate if bundle size exceeds MAX_PROMPT_BYTES
7) All ingest/API stages emit structured logs with `snapshot_etag` so traces are fully auditable end‑to‑end

## Q. Health & Auth
- **Health/Ready endpoints**: `GET /healthz` (process up) and `GET /readyz` (deps ready including ArangoDB) on every service
- **Auth & CORS**: Bearer/JWT at API edge, CORS allow-list for FE origin

## R. Summary: Key Implementation Priorities

### R1. Critical Path (Blocking Dependencies)
1. **ArangoDB setup** with graph collections and vector indexes
2. **Ingest pipeline** with JSON validation and field catalog generation
3. **Memory API** with normalized envelopes and k=1 expansion (AQL)
4. **Gateway orchestration** with evidence bundling and LLM validation
5. **Intent router** for natural language queries with function routing
6. **Frontend SSE streaming** with audit drawer

### R2. Quality Gates (Non-negotiable)
- All services have health/ready endpoints with ArangoDB readiness checks
- Golden tests pass with coverage=1.0 and completeness_debt=0
- Every request generates complete audit trail with deterministic fingerprints
- Schema-agnostic proof: new JSON fields appear without code changes
- Function routing correctly maps natural language to Memory API calls

### R3. Success Metrics
- **Performance**: p95 ≤3.0s for `/v2/ask` with known slugs, ≤4.5s for `/v2/query`
- **Reliability**: fallback_used rate <5% under normal load
- **Auditability**: 100% of requests have complete artifact retention
- **Developer experience**: docker-compose up → working system with ArangoDB in <5 minutes