# Complete Test Files List 

## Configuration Files
```
pytest.ini
conftest.py (project‐wide fixtures)
tests/conftest.py (pytest fixtures for tests/)
```

## Performance Tests (4 files)
```
tests/performance/
├── test_model_inference_speed.py
├── test_fallback_rate_under_load.py
├── test_query_latency.py
└── test_ask_latency.py
```

## Unit: Ingest (16+ files)
```
tests/unit/services/ingest/
├── test_backlink_derivation.py
├── test_contract_orphans.py
├── test_field_catalog_alias_learn.py
├── test_graph_upsert_idempotent.py
├── test_id_regex_schema_parity.py
├── test_ingest_health.py
├── test_new_field_normalization.py
├── test_normalize_empty_link_arrays.py
├── test_relation_catalog.py
├── test_snapshot_watcher.py
├── test_snippet_enricher.py
├── test_snippet_golden.py
├── test_strict_id_timestamp.py
├── test_validation_bad_id.py
└── test_validation_fixtures.py
```

## Unit: Memory API (7 files)
```
tests/unit/services/memory_api/
├── test_enrich_stubs.py
├── test_expand_and_resolve_contracts.py
├── test_expand_candidates_unit.py
├── test_memory_api_health.py
├── test_resolve_behaviors.py
├── test_schema_http_headers.py
└── test_timeouts.py ← duplicates basename with api_edge
```

## Unit: API Edge (6 files)
```
tests/unit/services/api_edge/
├── test_api_edge_health.py
├── test_auth_and_cors.py
├── test_rate_limit.py
├── test_resolve_text_vector.py
├── test_sse_streaming_integration.py
└── test_timeouts.py ← duplicates basename with memory_api
```

## Unit: Gateway (18 files)
```
tests/unit/services/gateway/
├── test_artifact_retention_comprehensive.py
├── test_back_link_derivations.py
├── test_evidence_builder_cache.py
├── test_gateway_audit_metadata.py
├── test_gateway_health.py
├── test_gateway_schema_mirror.py
├── test_llm_invalid_json_fallback.py
├── test_llm_retry_twice_fallback.py
├── test_match_snippet.py
├── test_prompt_builder_determinism.py
├── test_resolver.py
├── test_router_query.py
├── test_selector.py
├── test_selector_edge_cases.py
├── test_templater_ask.py
├── test_templater_golden.py
├── test_validator.py
└── test_validator_edgecases.py
```

## Unit: Core Packages (9 files)
```
tests/unit/packages/core_logging/
├── test_log_stage.py
└── test_snapshot_etag_logging.py

tests/unit/packages/core_utils/
├── test_snapshot.py
├── test_fingerprint.py
├── test_slugify.py
├── test_fp.py
└── test_ids.py

tests/unit/packages/core_validator/
├── test_validator_golden_matrix.py
└── test_validator_negative.py
```

## Integration & Ops (2 files)
```
tests/integration/
└── test_missing_coverage.py

tests/ops/
└── test_vector_index_bootstrap.py
```

---

## Summary
- **Total test files:** 62+ files
- **Configuration:** 3 files
- **Performance:** 4 files
- **Unit tests:** 56 files across services and packages
- **Integration/Ops:** 2 files

## Notes
- Duplicate basename warning: `test_timeouts.py` exists in both `memory_api` and `api_edge`
- Ingest service has the most comprehensive unit test coverage (16+ files)
- Gateway service follows closely with 18 test files
- Core packages are well-tested with 9 focused test files