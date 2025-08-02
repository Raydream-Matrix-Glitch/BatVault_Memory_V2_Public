# Milestone Requirements to Test Coverage Mapping

## Coverage Summary
- **Total test files discovered:** 73
- **✔️ Tests mapped to ≥ 1 Milestone 1-3 requirement:** 62
- **⚠️ Tests that do not map to any requirement:** 11 (usually __init__.py, fixtures, or generic health checks)
- **🎯 Coverage status:** **No Milestone 1–3 requirement is left without at least one covering test**

---

## Milestone 1 — Ingest V2 + Catalogs + Core Storage

| Requirement | Test Coverage |
|-------------|---------------|
| **Strict validation + normalisation per K-schemas** | `tests/unit/services/ingest/test_id_regex_schema_parity.py`<br>`tests/unit/services/ingest/test_strict_id_timestamp.py`<br>`tests/unit/services/ingest/test_validation_bad_id.py`<br>`tests/unit/services/ingest/test_validation_fixtures.py`<br>`tests/unit/services/ingest/test_normalize_empty_link_arrays.py`<br>`tests/unit/packages/core_utils/test_ids.py` |
| **Support new fields tags, based_on, snippet, x-extra** | `tests/unit/services/gateway/test_match_snippet.py`<br>`tests/unit/services/ingest/test_new_field_normalization.py`<br>`tests/unit/services/ingest/test_snippet_enricher.py`<br>`tests/unit/services/ingest/test_snippet_golden.py`<br>`tests/unit/services/ingest/test_field_catalog_alias_learn.py` |
| **Back-link derivation event.led_to ↔ decision.supported_by, etc.** | `tests/unit/services/ingest/test_backlink_derivation.py` |
| **Orphan handling for events/decisions** | `tests/unit/services/ingest/test_contract_orphans.py` |
| **Graph collections in Arango (decisions, events, transitions)** | `tests/unit/services/ingest/test_graph_upsert_idempotent.py`<br>`tests/ops/test_vector_index_bootstrap.py` |
| **768-d HNSW vector indexes** | `tests/ops/test_vector_index_bootstrap.py` |
| **AQL foundations for k = 1 traversal** | `tests/unit/services/memory_api/test_expand_candidates_unit.py` |
| **Idempotent node/edge upserts** | `tests/unit/services/ingest/test_graph_upsert_idempotent.py` |
| **/api/enrich/* normalised envelopes** | `tests/unit/services/api_edge/test_auth_and_cors.py`<br>`tests/unit/services/memory_api/test_enrich_stubs.py`<br>`tests/unit/services/memory_api/test_schema_http_headers.py` |
| **Field & Relation Catalog endpoints** | `tests/unit/services/gateway/test_gateway_schema_mirror.py`<br>`tests/unit/services/ingest/test_field_catalog_alias_learn.py`<br>`tests/unit/services/ingest/test_relation_catalog.py` |
| **snapshot_etag on every response** | `tests/unit/packages/core_logging/test_snapshot_etag_logging.py`<br>`tests/unit/packages/core_utils/test_snapshot.py`<br>`tests/unit/services/ingest/test_snapshot_watcher.py` |
| **Contract & cross-link tests (coverage = 1, completeness = 0)** | `tests/integration/test_missing_coverage.py` |

---

## Milestone 2 — Memory API k = 1 + Resolver + Caching

| Requirement | Test Coverage |
|-------------|---------------|
| **Real AQL k = 1 traversal** | `tests/unit/services/gateway/test_router_query.py`<br>`tests/unit/services/memory_api/test_expand_and_resolve_contracts.py` |
| **Performance budgets: Search ≤ 800 ms, Expand ≤ 250 ms, Enrich ≤ 600 ms** | `tests/performance/test_ask_latency.py`<br>`tests/performance/test_fallback_rate_under_load.py`<br>`tests/performance/test_model_inference_speed.py`<br>`tests/performance/test_query_latency.py`<br>`tests/unit/services/api_edge/test_rate_limit.py` |
| **Unbounded neighbour collection (truncation later)** | `tests/unit/services/memory_api/test_expand_and_resolve_contracts.py` |
| **Slug short-circuit resolver** | `tests/unit/packages/core_utils/test_slugify.py`<br>`tests/unit/services/gateway/test_resolver.py`<br>`tests/unit/services/gateway/test_router_query.py`<br>`tests/unit/services/memory_api/test_resolve_behaviors.py` |
| **BM25 text search resolver** | `tests/unit/services/memory_api/test_resolve_behaviors.py` |
| **Vector search behind ENABLE_EMBEDDINGS flag** | `tests/unit/services/api_edge/test_resolve_text_vector.py`<br>`tests/unit/services/gateway/test_resolver.py` |
| **Confidence scoring on resolution** | `tests/unit/services/gateway/test_resolver.py` |
| **Redis caches (5 min resolver, 1 min expand) w/ etag invalidation** | `tests/unit/services/memory_api/test_enrich_stubs.py` |
| **Cache metrics / hit rate tracking** | `tests/unit/services/gateway/test_evidence_builder_cache.py` |
| **OTEL spans for all stages** | `tests/unit/packages/core_logging/test_log_stage.py` | | `tests/unit/observability/test_stage_span_coverage.py` |
| **TTFB assertions ≤ 600 ms (slug) / ≤ 2.5 s (search)** | `tests/performance/test_ask_latency.py`<br>`tests/performance/test_query_latency.py` |
| **Stage-timeout graceful degrade** | `tests/performance/test_fallback_rate_under_load.py`<br>`tests/unit/services/api_edge/test_timeouts.py`<br>`tests/unit/services/memory_api/test_timeouts.py` |

---

## Milestone 3 — Gateway Evidence + Validator + Weak AI

| Requirement | Test Coverage |
|-------------|---------------|
| **Evidence builder gathers all k = 1 neighbours first** | `tests/unit/services/gateway/test_back_link_derivations.py`<br>`tests/unit/services/gateway/test_evidence_builder_cache.py` |
| **Truncate only if bundle > 8192 B; selector model decides drops** | `tests/unit/services/gateway/test_selector.py`<br>`tests/unit/services/gateway/test_selector_edge_cases.py` |
| **Deterministic baseline selector (recency + similarity)** | `tests/unit/services/gateway/test_selector.py`<br>`tests/unit/services/gateway/test_selector_edge_cases.py` |
| **Evidence cache 15 min TTL** | `tests/unit/services/gateway/test_evidence_builder_cache.py` |
| **Bi-encoder resolver + BM25 fallback** | *Note: Covered under Milestone 2 requirements* |
| **GBDT/log-reg evidence selector w/ feature extraction** | `tests/unit/services/gateway/test_selector.py`<br>`tests/unit/services/gateway/test_selector_edge_cases.py` |
| **Canonical Prompt Envelope + SHA-256 fingerprint** | `tests/unit/packages/core_utils/test_fingerprint.py`<br>`tests/unit/packages/core_utils/test_fp.py`<br>`tests/unit/services/gateway/test_prompt_builder_determinism.py`<br>`tests/unit/services/gateway/test_gateway_audit_metadata.py` |
| **Artifact retention in MinIO/S3 (envelope, prompt, raw LLM, etc.)** | `tests/unit/services/gateway/test_artifact_retention_comprehensive.py` |
| **Schema validation against WhyDecisionAnswer@1** | `tests/unit/packages/core_validator/test_validator_golden_matrix.py`<br>`tests/unit/services/gateway/test_validator.py`<br>`tests/unit/services/gateway/test_validator_edgecases.py` |
| **ID-scope & mandatory-citation checks** | `tests/unit/packages/core_utils/test_ids.py`<br>`tests/unit/packages/core_validator/test_validator_negative.py`<br>`tests/unit/services/gateway/test_validator.py`<br>`tests/unit/services/gateway/test_validator_edgecases.py` |
| **Deterministic templater fallback on validation failure** | `tests/unit/services/gateway/test_llm_invalid_json_fallback.py`<br>`tests/unit/services/gateway/test_llm_retry_twice_fallback.py`<br>`tests/unit/services/gateway/test_templater_ask.py`<br>`tests/unit/services/gateway/test_templater_golden.py` |
| **Evidence, model & bundle metrics surfaced** | `tests/unit/services/api_edge/test_api_edge_health.py`<br>`tests/unit/services/gateway/test_gateway_health.py`<br>`tests/ops/test_metrics_smoke.py`<br>`tests/unit/services/gateway/test_artifact_metric_names.py` |
| **Complete artifact trail per request** | `tests/unit/services/api_edge/test_sse_streaming_integration.py`<br>`tests/unit/services/gateway/test_gateway_audit_metadata.py` |
| **Performance target p95 ≤ 3 s for /v2/ask slug** | `tests/performance/test_ask_latency.py` |

---

## Unmapped Tests (11 files)

These test files exist but do not target any explicit Milestone 1-3 requirement:

- `tests/conftest.py`
- `tests/unit/packages/core_logging/__init__.py`
- `tests/unit/packages/core_validator/__init__.py`
- `tests/unit/packages/core_utils/__init__.py`
- `tests/unit/services/api_edge/__init__.py`
- `tests/unit/services/gateway/__init__.py`
- `tests/unit/services/ingest/__init__.py`
- `tests/unit/services/ingest/test_ingest_health.py`
- `tests/unit/services/memory_api/__init__.py`
- `tests/unit/services/memory_api/conftest.py`
- `tests/unit/services/memory_api/test_memory_api_health.py`

*Note: Health-check tests are useful but were not called out as Milestone requirements.*

---