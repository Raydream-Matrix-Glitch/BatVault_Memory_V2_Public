# Batvault Codebase Analysis - File Structure & Milestone Status

## 1. File-map (What Lives Where & Why)

| Top-level | Key Contents | Purpose / Notes |
|-----------|-------------|-----------------|
| `/services/api_edge` | app.py, minimal FastAPI wiring, CORS, idempotency hash helper, healthz / readyz routes, MinIO probe, tests | First-hop HTTP edge – no business logic |
| `/services/gateway` | evidence.py, selector.py, prompt_envelope.py, templater.py, load_shed.py, app.py, tests | Orchestrator that builds evidence bundles, applies selector-truncate, builds canonical prompt, writes artifacts to MinIO, falls back to templater |
| `/services/ingest` | pipeline/ (normalize, derive_links, snippet_enricher, graph_upsert), catalog/field_catalog.py, cli.py, tests | V2 ingest ⇒ validates JSON, normalizes/aliases fields, derives backlinks, upserts to Arango, emits snapshot_etag, maintains Field / Relation catalog |
| `/services/memory_api` | app.py (enrich endpoints, k=1 expand, text resolve), store/ adapters, tests | Normalized envelopes & graph operations (stubbed vector search) |
| `/packages/core_logging` | structured OTEL logger | Shared logging util |
| `/packages/core_models` | Pydantic response contracts | Wire contracts for /v2/ask responses etc. |
| `/packages/core_utils` | ids.py (deterministic IDs, slugify_id), fingerprints.py, snapshot.py | Common helpers – should be single source of truth |
| `/packages/core_config` | constants.py, settings.py | Central config & feature-flags (ENABLE_EMBEDDINGS, budgets…) |
| `/packages/core_storage` | arangodb.py | Thin async wrapper around Arango; stubs for BM25 & vector search |
| `/packages/core_validator` | validator.py + tests | Schema / ID-scope validator used by gateway |
| `/packages/link_utils` | derive_links.py | Generic backlink derivation reused by ingest |
| `/memory/fixtures/**` | sample decisions / events / transitions | Seed corpus for local runs & tests |
| `/ops` | docker-compose.yml, OTEL collector, bootstrap scripts | Local infra |
| `/scripts` | seed_memory.sh, smoke checks | Convenience tooling |

*(183 files total; full exhaustive listing omitted for brevity but available on request)*

## 2. Duplicate Logic Currently in the Tree

| What | Where Duplicates Live | Comment / Risk |
|------|----------------------|----------------|
| `slugify_id()` implementation | `packages/core_utils/ids.py` and `services/ingest/pipeline/normalize.py` | Normalization rules drift risk; consolidate into core_utils and import everywhere |
| Health-endpoint handler (healthz) | Each service has an identical local copy | Acceptable, but could be a shared FastAPI dependency to avoid divergence |
| `enrich_*` helper trio (enrich_decision / event / transition) | Memory-API app.py and Ingest snippet_enricher.py | Same summarization logic duplicated; move to a shared package (e.g. core_enrich) |
| `main()` entry shims | CLI inside ingest/cli.py, ops/bootstrap_arangosearch.py, scripts/check_embedding_config.py | Low risk but stylistic duplication – OK |
| ID-regex & timestamp validators | Tests in core_validator and ingest tests replicate regex and parsing helpers | Keep only in core_validator and import in tests |

*No other identical functions or copy-pasted blocks were detected (AST diff across all .py files)*

## 3. Gap Analysis Against Milestones 0 → 3

### M0 – Bootstrapping & Health
**Deliverables:**
- Service skeletons
- /healthz & /readyz
- Deterministic IDs + logging
- Docker Compose w/ Arango, Redis, MinIO

**Status:** ✔ **Done**

**Notes:** CI workflow present; smoke tests exist.

### M1 – Ingest V2 & Catalogs
**Deliverables:**
- Strict JSON validation incl. new fields (tags, based_on, snippet, x-extra)
- Backlink derivation
- snapshot_etag publishing
- Field / Relation catalogs

**Status:** ✔ **Mostly Done**

**Notes:** Validation rules implemented; derive_links.py covers reciprocity; Field/Relation endpoints live. Small gap: tag normalization not yet slug-lower-sorted in normalize.py.

### M2 – Memory-API k=1 + Resolver + Redis Cache
**Deliverables:**
- Async AQL traversal
- Slug short-circuit + BM25 resolver
- Redis TTL caches
- Performance budgets enforced

**Status:** ✔ **Implemented (baseline)**

**Notes:** k=1 traversal in core_storage.arangodb; Redis used via core_config.settings. Vector search flag exists but embedding encoder not wired – acceptable (flag default False).

### M3 – Gateway Evidence + Validator + Weak AI
**Deliverables:**
- Unbounded neighbor collect + selector-truncate
- Deterministic selector baseline
- Canonical prompt envelope & SHA-256 fingerprint
- MinIO artifact sink
- Schema validator + deterministic fallback

**Status:** ◑ **Partial**

**Notes:** Evidence builder & selector working; prompt envelope + artifact uploads present. 

**Gaps:**
1. Bi-encoder & Cross-encoder resolver models not integrated (only BM25)
2. Selector is deterministic only – GBDT/LogReg hooks not yet stubbed
3. core_validator is invoked in tests but not yet wired into gateway.app request flow
4. Cache-invalidation on snapshot_etag exists for resolver but not for evidence cache