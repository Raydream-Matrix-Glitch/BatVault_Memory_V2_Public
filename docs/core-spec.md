# V2 Batvault Memory - Core Specification

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

## 4. API Contracts

### 4.1. Structured Endpoint

```http
POST /v2/ask
{
  "intent": "why_decision|who_decided|when_decided|chains",
  "decision_ref": "pause-paas-rollout-2024-q3",
  "options": {"llm_mode": "auto|off|force", "timeout_ms": 3500}
}
```

### 4.2. Natural Language Endpoint

```http
POST /v2/query
{
  "text": "Why was the PaaS rollout paused?"
}
```

**Function Routing**:
- `search_similar(query_text: string, k: int=3)` → vector search
- `get_graph_neighbors(node_id: string, k: int=3)` → graph traversal

### 4.3. Response Schema

```json
{
  "intent": "why_decision",
  "evidence": {
    "anchor": {"id": "...", "option": "...", "rationale": "..."},
    "events": [{"id": "...", "summary": "...", "timestamp": "..."}],
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
    "latency_ms": 1250
  }
}
```

## 5. Evidence Rules & Quotas

### 5.1. Intent Quotas

| Intent | k | Events | Preceding | Succeeding |
|--------|---|--------|-----------|------------|
| `why_decision` | 1 | unbounded* | unbounded* | unbounded* |
| `who_decided` | 1 | unbounded* | 0 | 0 |
| `when_decided` | 1 | unbounded* | unbounded* | unbounded* |
| `chains` | unlimited | unbounded* | N/A | N/A |

*Gateway may truncate to meet prompt-size or latency budgets; logs `selector_truncation=true` when evidence is dropped.

### 5.2. Validation Rules

**Response Validation**:
- `supporting_ids ⊆ allowed_ids` (strict subset)
- `anchor.id` must appear in `supporting_ids`
- Present transition IDs must be cited

**Artifact Validation** (Ingest):

| Field | Rule | Purpose |
|-------|------|---------|
| `id` | `/^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$/` | Unique lookup |
| `timestamp` | ISO-8601 UTC (`Z`) | Ordering/recency |
| Content fields* | ≥1 non-whitespace char | Prevents empty stubs |

*Content fields: `rationale`, `description`, `reason`, `summary`

**Link Validation** (Updated):
If `supported_by`, `led_to`, `transitions`, `from`, or `to` **exist and are non-empty**, each ID must be resolvable; otherwise the field may be omitted or an empty array. This allows for orphaned events (no `led_to` yet) and standalone decisions (no `transitions`).

## 6. JSON Authoring Schemas

### 6.1. Decision (with optional/empty arrays)
```json
{
  "id": "pause-paas-rollout-2024-q3",
  "option": "Pause PaaS rollout",
  "rationale": "Q2 financials revealed cashflow guard-rails...",
  "timestamp": "2024-07-20T14:30:00Z",
  "decision_maker": "Bob",
  "supported_by": ["B-E1", "B-E2"],
  "transitions": []  // Empty array is valid - no succeeding decision yet
}
```

### 6.2. Event (orphan example)
```json
{
  "id": "B-E1",
  "summary": "Q2 report shows 40% infra overspend",
  "description": "Q2 financial report shows...",
  "timestamp": "2024-07-19T08:00:00Z",
  "led_to": []  // Empty array is valid - no decision made yet based on this event
}
```

### 6.3. Transition
```json
{
  "id": "trans-123",
  "from": "enter-cloud-market-2024-q1",
  "to": "pause-paas-rollout-2024-q3",
  "relation": "causal",
  "reason": "Guard-rail breached...",
  "timestamp": "2024-08-12T09:05:00Z"
}
```

### 6.4. Orphan Handling Examples

**First Decision (no predecessor)**:
```json
{
  "id": "initial-cloud-decision-2024",
  "option": "Enter cloud market",
  "rationale": "Market opportunity identified...",
  "timestamp": "2024-01-15T10:00:00Z",
  "decision_maker": "Alice",
  "supported_by": ["market-research-event"],
  "transitions": []  // No preceding decision
}
```

**Pending Event (no decision yet)**:
```json
{
  "id": "pending-security-audit",
  "summary": "Security audit reveals vulnerabilities",
  "description": "Annual security audit identified critical vulnerabilities...", 
  "timestamp": "2024-07-25T14:00:00Z",
  "led_to": []  // No decision made yet - still being evaluated
}
```

## 7. Memory API Endpoints

- `GET /api/enrich/{type}/{id}` → normalized envelope
- `POST /api/graph/expand_candidates` → k=1 traversal (AQL)
- `POST /api/resolve/text` → vector search (ArangoDB)
- `GET /api/schema/fields` → field catalog
- `GET /api/schema/rels` → relation catalog

## 8. Prompt & Audit System

### 8.1. Prompt Envelope
```json
{
  "prompt_version": "why_v1",
  "intent": "why_decision",
  "question": "Why was the PaaS rollout paused?",
  "evidence": { /* minimal bundle */ },
  "allowed_ids": ["pause-123", "event-1"],
  "constraints": {
    "output_schema": "WhyDecisionAnswer@1",
    "max_tokens": 256
  }
}
```

### 8.2. Deterministic IDs
- `prompt_fingerprint`: SHA-256 of canonical envelope
- `snapshot_etag`: Content hash + timestamp of JSON corpus
- `bundle_fingerprint`: SHA-256 of evidence bundle
- `request_id`: Per-request trace ID

### 8.3. Artifacts (per request_id)
- Prompt envelope (canonical JSON)
- Rendered prompt (exact LLM input)
- Raw LLM JSON output
- Validator report
- Final response JSON

## 9. Performance & Reliability

### 9.1. Performance Targets
- **TTFB**: ≤600ms (known slug), ≤2.5s (search)
- **p95 Total**: ≤3.0s (`/v2/ask`), ≤4.5s (`/v2/query`)
- **Model Inference**: ≤5ms (resolver), ≤2ms (selector)

### 9.2. Fallback Strategy
1. **LLM Failures**: Auto-retry ≤2 times → templated answer
2. **Model Failures**: Weak AI → deterministic methods
3. **Timeout**: Return partial evidence with `completeness_flags`

### 9.3. Caching
- **Resolver**: 5min TTL
- **Evidence Bundle**: 15min TTL, invalidate on `snapshot_etag` change
- **LLM JSON**: 2min TTL for hot anchors

## 10. Configuration & Intent Registry

**Intent Registry**: Policy-as-data configuration via `registry.json` (JSON format for consistency, not watched by snapshot watcher)

**Environment**: `SNAPSHOT_EXT=json`, watcher globs `**/*.json`

## 11. Acceptance Criteria

### 11.1. Functional Requirements
1. `/v2/ask` with known slug returns in ≤3.0s p95, `fallback_used=false`
2. `/v2/query` natural language routing works for basic intents
3. Invalid LLM JSON triggers fallback, never returns user-visible errors
4. New JSON fields auto-appear in `/v2/schema/fields` (zero code changes)

### 11.2. Quality Gates
5. Golden tests pass: `coverage=1.0`, `completeness_debt=0`
6. All responses include audit metadata: `prompt_id`, `policy_id`, `prompt_fingerprint`
7. Complete artifact retention for every request
8. Health endpoints ready with ArangoDB dependency checks

### 11.3. Data Quality & Orphan Handling
9. All timestamps in UTC (`Z`) format
10. Event summary auto-repair if missing/empty
11. Cross-link reciprocity: `event.led_to ↔ decision.supported_by` (where both exist)
12. k=1 expansion collects all neighbours; gateway may truncate for prompt‑size or latency budgets (logs selector_truncation=true).
13. Orphaned events and decisions are valid and handled gracefully
14. Empty link arrays (`[]`) are valid; omitted fields are treated as empty arrays

## 12. Service Structure

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
/memory
  /{decisions,events,transitions}/*.json
```

## 13. Development Setup

**Prerequisites**: Docker Compose with ArangoDB, Redis, MinIO

**Quick Start**:
```bash
docker-compose up -d
./scripts/seed_memory.sh  # Load /memory/*.json
./scripts/smoke.sh        # E2E health check
```

**Environment**: `SNAPSHOT_EXT=json`, watcher globs `**/*.json`

## 14. Missing Test Cases (Implementation TODO)

### 14.1. Validator Unit Tests
- `decision_no_transitions.json` (empty array validation)
- `event_orphan.json` (no `led_to` validation)
- `decision_missing_transitions_field.json` (omitted field validation)

### 14.2. Golden Test Cases
- `why_decision_orphan_event.json` (should return empty transitions, single event)
- `why_decision_standalone.json` (first decision, no predecessors)
- `why_decision_many_events.json` (decision with >3 supporting events, tests unbounded collection)
- `why_decision_branching_transitions.json` (multiple preceding/succeeding decisions)

### 14.3. Router Contract Tests
- `test_query_intent.py` → text: "Why did we pause PaaS?" → expects Memory API calls + final WhyDecisionResponse@1 body
- Ensure `/v2/query` triggers both `search_similar` AND `get_graph_neighbors` calls and merges results

### 14.4. Back-link Derivation Tests
- Snapshot where only one side exists → ingest logs `link_missing` warning rather than failing
- Test bidirectional link repair when backlinks are missing

This specification defines the core system contracts and requirements with explicit orphan handling. See `/docs/adr/` for architectural decisions, ML roadmap, and detailed UX specifications.