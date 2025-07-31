# Project Development Milestones

## Milestone 0 — Bootstrapping & Health (Day 1)

**Goal:** `docker-compose up` → ArangoDB, Redis, MinIO + 4 Python services boot (FastAPI), health/ready endpoints, structured logging, deterministic IDs, repo layout scaffolding, CI baseline, smoke test.

### Key Deliverables

**Repository Structure:**
- Repo scaffold matching `/services`, `/packages`, `/ops`, `/scripts`, `/memory` as per spec

**Services:**
- **/services/api-edge**: Basic routes `/healthz`, `/readyz`, request idempotency keys (hash of body), SSE stub
- **/services/gateway**: Route shells, trace envelope, artifact sink to MinIO, templater fallback implementation (no LLM yet)
- **/services/memory-api**: Stubs for `/api/enrich/*`, `/api/schema/*`, `/api/graph/expand_candidates`, `/api/resolve/text` returning fixture data
- **/services/ingest**: JSON loader + schema validation (ID regex, timestamps, content fields) and field/relation catalog from `/memory/*`

**Core Packages:**
- **/packages/core-***: Logging (JSON), IDs, config, errors, models (Pydantic v2) with response contracts

**Operations:**
- **/ops**: Dockerfiles per service, docker-compose (includes ArangoDB/Redis/MinIO), OTEL collector

**Scripts:**
- **/scripts**: `seed_memory.sh` (loads sample memory/ fixtures), `smoke.sh` (pings health endpoints)

**CI/CD:**
- GitHub Actions (lint, unit), pinned Python 3.11 / Node 20

**Outcome:** You can run health checks and see structured logs & IDs end-to-end. This prepares the ground for ingest and real graph queries.

---

## Milestone 1 — Ingest V2 + Catalogs

**Goal:** Turn the sample JSON into a real Arango snapshot with `snapshot_etag`; enforce authoring rules; publish Field/Relation Catalog; idempotent upserts and cross-link repair.

### Key Deliverables

- Strict validation + normalization per K-schemas (aliases, text normalization, UTC timestamps)
- Back-link derivation (`event.led_to` ↔ `decision.supported_by`; transitions appear in both decisions)
- Arango upsert (graph) and optional vector embeddings pre-wiring (config-toggled for now)
- Memory API serves normalized envelopes + catalogs; headers include `snapshot_etag`
- Contract tests for orphan & empty‑array cases must run in CI (coverage ≥1, completeness_debt = 0).

---

## Milestone 2 — Memory API k=1 & Resolver

**Goal:** Real AQL for /api/graph/expand_candidates (k = 1); BM25 resolver with optional vector flag; Redis caching; stage‑level performance budgets.

### Key Deliverables

- Implement resolver pipeline: known slug → skip; else text→id using BM25 first; vector search behind a feature flag
- Performance budgets + timeouts at stage level:
  - Search: 800ms
  - Expand: 250ms
  - Enrich: 600ms
- Unit + contract tests for evidence collection k=1
- Resolver
  slug short‑circuit → else BM25 → optional vector (ENABLE_EMBEDDINGS=true).
- Expand
  AQL k = 1 traversal, caches results (CACHE_TTL_EXPAND_SEC, default 60 s).
- Performance Budgets
  Search ≤ 800 ms, Expand ≤ 250 ms, Enrich ≤ 600 ms, enforced via asyncio.wait_for.
- Cache Policy
  TTLs now enumerated here (spec §9.3): Resolver 5 min, Evidence 15 min, LLM JSON 2 min.
- TTFB Gates
  CI asserts ≤ 600 ms (known slug) / ≤ 2.5 s (search) p95.
- Tests
  Unit + contract tests for resolver & k = 1 expand; smoke covers ETag propagation.
- Observability
  OTEL spans on all M2 stages.

---

## Milestone 3 — Gateway Evidence & Validator

**Goal:** Evidence bundling (unbounded collect, truncate only if `>MAX_PROMPT_BYTES` with selector), canonical prompt envelope + fingerprints, validator + deterministic fallback.

### Key Deliverables

**Evidence Selection:**
- Selector: deterministic baseline (recency + similarity)
- Selector logs (`selector_model_id`, `selector_truncation`, dropped IDs)

**Prompt Management:**
- Canonical Prompt Envelope + `prompt_fingerprint`
- Artifact retention of envelope, rendered prompt, raw LLM JSON, validator report, final response

**Validation:**
- Golden tests for templater answers
- Strict validation rules (`supporting_ids ⊆ allowed_ids`, anchor cited, transitions cited)

**Additions**
- Evidence Builder
  Caches bundles 15 min (invalidated on snapshot_etag change).
- Selector
  Deterministic baseline (recency + similarity) with selector_truncation logs.
- Prompt Management
  Canonical Envelope, prompt_fingerprint, artifact retention (Envelope → Prompt → Raw LLM → Validator → Final).
- Validation
  Rules in Core‑Spec §11 enforced; completeness_flags included in every response.
- Golden Tests
  Templates + validator golden tests run in CI; coverage ≥1, completeness_debt = 0.
- TTFB Impact
  p95 total ≤ 3 s (/v2/ask slug) / ≤ 4.5 s (/v2/query).

---

## Milestone 4 — /v2/ask & /v2/query + Routing

**Goal:** Full request path: NL routing → Memory API calls → evidence → LLM (optional) → validation → stream short answer via SSE; policy registry.

### Key Deliverables

**Intent Routing:**
- Function-routing calling `search_similar` + `get_graph_neighbors`
- Merge results, include `routing_confidence`
- Intent Router with function‑routing.

**Policy Management:**
- `policy_id` / `prompt_id` registry.json
- Retries with jitter
- `fallback_used` semantics
- Policy Registry with retries & fallback_used.

**Performance:**
- Performance: p95 & cache TTLs re‑asserted.

**Additions**
- Load-shedding mechanisms
- Auto Load‑Shedding & Circuit Breakers (Tech‑Spec § N) — llm_mode=off when budgets breach.


---

## Milestone 5 — Frontend App (SSE + Audit Drawer)

**Goal:** Next.js app with a simple query UI, token streaming, and an "Audit Drawer" showing envelope → rendered prompt → raw LLM JSON → validator → final response.

### Key Deliverables

- CORS/auth wiring at API edge
- OTEL traces end-to-end
- Real-time streaming interface
- Comprehensive audit trail visualization

---

## Milestone 6 — Harden & Prove

**Goal:** Golden suites (why/who/when), e2e compose tests, load-shedding, cache-hit metrics, error budgets.

### Key Deliverables

- Comprehensive test suites covering core question types
- End-to-end Docker Compose testing
- Cache performance metrics
- Error budget monitoring and alerting
- Golden suites for Why/Who/When + e2e compose tests.
- Cache‑hit & latency dashboards.
- Error budget SLOs.