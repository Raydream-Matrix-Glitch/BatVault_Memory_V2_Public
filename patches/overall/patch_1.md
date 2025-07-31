# Batvault V2 Implementation Tasks

## A. Hot-fixes (Blockers)

| ID | Task | Correct File(s) | Acceptance Test |
|----|------|----------------|-----------------|
| A-1 | SHA-256 fingerprint bug | `packages/core_utils/src/core_utils/fingerprints.py` (line ≈ 22) | add `tests/core/test_fingerprint.py` |
| A-2 | ID regex allows "_" | `services/ingest/src/ingest/pipeline/normalize.py` **+** `services/memory_api/src/memory_api/app.py` | new ingest unit test |
| A-3 | duplicate `setdefault` | `services/memory_api/src/memory_api/app.py` (lines 214-221) | flake-8/B006 passes |

## B. Data-model & Ingest (Milestone 1)

| ID | Task | Correct File(s) / Notes |
|----|------|------------------------|
| B-1 | `based_on ↔ transitions` reciprocity | **create** `services/ingest/src/ingest/pipeline/derive_links.py` |
| B-2 | new fields (`snippet`, `x-extra`) | update schemas in `services/ingest/src/ingest/schemas/json_v2/*.json` + enrich logic in `memory_api/app.py` |
| B-3 | 768-d vector index | **add** `ops/bootstrap_arango_vector.py` (or JS script) |
| B-4 | `/api/schema/rels` live endpoint | **create** `services/memory_api/src/memory_api/routes/schema.py` |

## C. Resolver Intelligence (Milestones 2-3)

| ID | Task | Correct File(s) |
|----|------|----------------|
| C-1 | BM25 resolver | `services/gateway/src/gateway/resolver/bm25.py` (new) |
| C-2 | Bi-encoder resolver | `services/gateway/src/gateway/resolver/embedding_model.py` (new) |
| C-3 | Vector upsert service | `services/ingest/src/ingest/pipeline/vector_upsert.py` (new) |
| C-4 | Selector ML upgrade | `services/gateway/src/gateway/selector_model.py` (create alongside existing `selector.py`) |
| C-5 | Confidence scoring | modify the two resolver modules above |

## D. Evidence Pipeline & Artifacts (Milestone 3)

| ID | Task | Correct File(s) / Notes |
|----|------|------------------------|
| D-1 | emit evidence metrics | `services/gateway/src/gateway/selector.py` → call `core_metrics.emit()` (package `packages/core_metrics/…` must be added) |
| D-2 | MinIO writer | `packages/core_artifacts/src/core_artifacts/minio_writer.py` (new) |
| D-3 | MinIO in compose | extend `ops/docker/docker-compose.yml` |
| D-4 | golden fixtures | `services/gateway/tests/golden/*.json` |

## E. Caching, Budgets & Timeouts (Milestone 2)

| ID | Task | Correct File(s) / Notes |
|----|------|------------------------|
| E-1 | snapshot-aware cache | **create** `packages/core_cache/src/core_cache/__init__.py` with `get_or_set()` |
| E-2 | expand cache TTL | decorator lives where E-1 is implemented |
| E-3 | hit/miss counters | same module + `packages/core_metrics` |
| E-4 | per-stage timeouts | wrap awaits in `memory_api/app.py`, `gateway/*` |
| E-5 | TTFB gate | `.github/workflows/ci.yml` |

## F. Observability

| ID | Task | Correct File(s) |
|----|------|----------------|
| F-1 | span names | `packages/core_logging/src/core_logging/logger.py` |
| F-2 | Grafana panel | dashboards only (no code) |

## G. CI / Tests / Docs

*(paths unchanged; still valid)*

---

## Key Takeaways

1. **No functional regressions**: Every gap captured in patch 0 still exists in the snapshot.

2. **Path fixes**: All mismatches were the result of the repo's `src/` layout (e.g. `packages/core_utils/src/…`) or missing folders that must be created.

3. **New helper packages required**: 
   - `packages/core_metrics`
   - `packages/core_cache` 
   - `packages/core_artifacts`
   
   Add these stubs before wiring emitters/writers.

4. **Everything else in patch 0 remains accurate** after validation.

You can safely update patch 0 with the bolded paths above and proceed with implementation in that order.