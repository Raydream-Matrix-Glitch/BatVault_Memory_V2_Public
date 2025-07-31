Project Tasks Document
A. Hot-Fixes (Blockers for Every Pipeline Run)



ID
Task
Files / Lines
Acceptance Test



A-1
Fix SHA-256 fingerprint bug (bytes already encoded)
packages/core_utils/fingerprints.py: line ~22hashlib.sha256(canon).hexdigest()
tests/core/test_fingerprint.py::test_fingerprint_hashes passes


A-2
Allow underscores in canonical IDs (spec §15.3)
services/ingest/src/ingest/pipeline/normalize.pyID_RE = r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$"+ mirror change in memory_api/app.py
New unit test creates doc alpha_bravo and ingests w/out 422 error


A-3
De-dupe noisy setdefault calls
services/memory_api/app.py: matches & vector QQvector_used
pytest -q shows no flake8-B006 warnings


B. Data-Model & Ingest Completeness (Milestone 1)



ID
Task
Files / Notes
Tests



B-1
Reciprocal based_on ↔ transitions
Enhance ingest/pipeline/derive_links.py::derive_links()
Contract test asserts both directions


B-2
Back-fill new fields (snippet, x-extra)
Update JSONSchema (schemas/v2/*.json) + getters in graph_store.py
Golden fixture includes all new fields


B-3
Vector HNSW(768) index
Add ops/arangodb-init.d/02_vector_index.jsCREATE VECTOR INDEX evidence_vec …
arangosh script idempotent; index visible via /_api/index


B-4
/api/schema/rels endpoint
Implement live graph introspection in memory_api/routes/schema.py
cURL returns ≥ 4 relation types


C. Resolver Intelligence (Milestone 2 & 3)



ID
Task
Details
Tests / Metrics



C-1
Full BM25 resolver
Replace stub in gateway/resolver/bm25.py with AQL:FOR d IN decisions SEARCH ANALYZER(BM25(d.text) > 0, 'text_en') …
Resolver returns ≥ 1 result for phrase query


C-2
Bi-encoder resolver (flagged)
New gateway/resolver/embedding_model.py wraps sentence-transformers/all-MiniLM-L6-v2; add env ENABLE_EMBEDDINGS=true
End-to-end test behind --embeddings flag; latency budget < 400 ms


C-3
Vector upsert service
Flesh out services/vector_upsert.py; post-ingest hook publishes doc + vector to Arango
CI verifies _vector attr present for 95% of docs


C-4
Selector ML upgrade (GBDT)
Train LightGBM on click logs, export to model/selector_lgbm.txt; load behind ENABLE_SELECTOR_ML
A/B test shows ↑MAP@5 vs deterministic baseline


C-5
Confidence scoring
Replace hard-code (0.5) with sigmoid of BM25 / cosine / recency; expose in JSON
pydantic validates 0 ≤ confidence ≤ 1


D. Evidence Pipeline & Prompt Artifacts (Milestone 3)



ID
Task
Files / Notes
Verification



D-1
Emit evidence metrics
In selector/truncate.py call core_metrics.emit('selector_truncation', …)
Grafana chart > 0 after load-test


D-2
Canonical prompt → MinIO
New pkg core_artifacts/minio_writer.py; persist:• canonical envelope JSON• rendered prompt.txt• raw LLM JSON• validator report
Integration test: object list = 4 per request_id


D-3
MinIO docker-compose
Add minio: service + creds to .env.example; health-check waits for bucket
docker compose up; health endpoint 200


D-4
Golden fixtures for Why/Who/When
Import 4 JSONs in gateway/tests/golden/; update pytest helper
pytest --golden passes


E. Caching, Budgeting & Timeouts (Milestone 2)



ID
Task
Files / Approach
Tests



E-1
Snapshot-aware cache invalidation
Extend core_cache.get_or_set to hash snapshot_etag; delete on change
Unit test toggles etag and expects cache miss


E-2
Expand cache TTL = 60 s respected
Ensure @cached(ttl=60) decorator present; test TTL expiry
Time-travel test (freezegun)


E-3
Hit/miss counters
core_metrics.emit('cache_hit', 1) in wrapper
Grafana shows non-zero hit rate


E-4
Per-stage timeouts
Wrap awaitables in asyncio.wait_for(..., STAGE_TIMEOUTS['expand']); bubble TIMEOUT
Integration test simulates 3 s delay → 504


E-5
TTFB ≤ 700 ms CI gate
Re-enable GitHub Action; run 50 sample queries, assert 95-th ≤ 700 ms
CI badge green


F. Observability & OTEL



ID
Task
Details
Done-when



F-1
Meaningful span names
core_logging.log_stage(span_name=…) for resolve, expand, bundle, prompt, llm, validate, render
Jaeger UI shows full trace tree


F-2
Evidence metric dashboard
Add Grafana panel: selector > MAX_BYTES, cache hit ratio
Dashboard shows real-time data


G. CI, Tests & Docs



ID
Task
Activity
Pass Criteria



G-1
Un-skip missing tests
Add for: based_on reciprocity, x-extra, vector search, timeouts
pytest -q passes 100%


G-2
Coverage ≥ 90%
Mark untested modules; enforce via coverage.xml in CI
Coverage job green


G-3
Perf test harness
scripts/perf/run_locust.sh hitting /api/resolve 1k RPS
P99 < 1.2 s


G-4
README & docs refresh
Document new env flags, MinIO, embedding model size, perf budgets
PR merged after tech-writer review


H. Optional / Stretch (Quick Wins)



ID
Idea
Rationale



H-1
Auto-downgrade to BM25 if vector index missing
Ensures cold clusters still answer


H-2
Lightweight online-A/B switch (/api/admin/variant?model=ml)
Safe rollout of GBDT selector


H-3
Async MinIO uploads via background tasks
Cut P99 latency by ~30 ms


Dependency Graph (Simplified)
graph TD
    A1[A-1] --> B1
    A2[A-2] --> B1
    A3[A-3] --> C1
    B1[B-1] --> B2
    B2[B-2] --> B3
    B3[B-3] --> C3
    C1[C-1] --> C2
    C2[C-2] --> C3
    D1[D-1] --> D2
    D2[D-2] --> D3
    E1[E-1] --> E3
    E2[E-2] --> E3
    E3[E-3] --> F1
    F1[F-1] --> F2
    E4[E-4]
    E5[E-5]
    G1[G-1] --> G2
    G2[G-2] --> G3
    G4[G-4]
