# Verified Gaps to Close Before Milestone 3 Sign-off

## Critical Implementation Gaps (A-Series)

### A-1: No Rate-Limiting Middleware at API-Edge
**Issue**: Only CORS + idempotency hash present.

**Why it matters**: API-edge is explicitly responsible for *"auth, **rate-limit**, idempotency"* in the Core-spec component table.

### A-2: Stage-Timeout Enforcement Missing in Gateway
**Issue**: Search, expand, enrich, LLM, validator run unbounded.

**Why it matters**: Budgets are normative (*Search 800 ms, Expand 250 ms, …*) in Tech-spec §H2 and Milestone 2 demands "performance budgets enforced".

### A-3: Vector-Search Path Can't Be Enabled
**Issue**: Milestone 1 requires a *768-d HNSW index* and a feature flag-gated resolver, but:
- `ENABLE_EMBEDDINGS` flag is absent from `core_config`
- `ops/bootstrap_arangosearch.py` isn't wired into any startup flow, so the index never exists

**Why it matters**: Required for Milestone 1 vector search capability.

### A-4:    
**Issue**: Only BM25 fallback implemented.

**Why it matters**: Listed as a Weak-AI deliverable for Milestone 3.

### A-5: Selector "Weak-AI" Model Absent
**Issue**: Deterministic sort only and therefore no `selector_model_id` in logs.

**Why it matters**: Same milestone item: *"Evidence selector: GBDT/log-reg for truncation decisions"*.

### A-6: Evidence-Bundle Metrics Not Emitted
**Issue**: `total_neighbors_found`, `selector_truncation`, etc. missing.

**Why it matters**: Mandatory in Observability & Audit section and reiterated in milestone checklist.

### A-7: Evidence-Cache Key Ignores `snapshot_etag`
**Issue**: Results in stale bundles after new ingest.

**Why it matters**: Spec 11.3 defines cache key must include `snapshot_etag` and invalidate on change.

### A-8: Graph-Upsert Idempotency Script Never Called
**Issue**: `graph_upsert.py` never called in CI / bootstrap, so real Arango data can drift from fixtures.

**Why it matters**: Idempotent upserts are a Milestone 1 deliverable.

## Code Quality & Maintenance Gaps (B-Series)

### B-1: Duplicate `slugify_id()` Implementations
**Issue**: Exists in both `packages/core_utils.ids` and `services/ingest/pipeline/normalize.py`.

**Why it matters**: Violates single-source rule; spec §L2 points to one canonical slug generator.

### B-2: Tags Not Slug-Lower-Sorted During Normalization
**Issue**: `normalize.py` only lower-cases.

**Why it matters**: Required by the same normalization rule.

### B-3: Three Trivial `main()` Entry Shims Duplicated
**Issue**: Duplicated across scripts; low-risk but still DRY debt.

**Why it matters**: Not spec-blocking; call-out from code audit.

### B-4: Shared Helpers Duplicated
**Issue**: `derive_links.py`, `ensure_bucket()`, health-endpoint handlers duplicated.

**Why it matters**: Causes silent drift; no one canonical import path (code audit).