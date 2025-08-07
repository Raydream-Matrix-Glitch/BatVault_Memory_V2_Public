Root-Cause 1 – Selector starts pruning only at the hard limit
Failing tests: test_selector_truncates
Suspect file: services/gateway/src/gateway/selector.py
Explanation: truncate_evidence() exits early when bundle_size_bytes <= SELECTOR_TRUNCATION_THRESHOLD, but the prune-loop keeps dropping items only until it is below MAX_PROMPT_BYTES (8192). A bundle of ~6.5 KB therefore passes through untouched (meta["selector_truncation"] == False) even though the spec says pruning must begin once the 6 144 B soft limit is crossed .
Fix plan: make the while-loop condition target SELECTOR_TRUNCATION_THRESHOLD, update the meta flag, and add a unit test with a 6.5 KB fixture.
Root-Cause 2 – Neighbors are discarded because the builder expects a "type" field
Failing tests: test_back_link_derivations, test_neighbor_contract_shapes
Suspect file: services/gateway/src/gateway/evidence.py
Explanation: The Memory-API stub returns {"neighbors":[ {...event…}, {...transition…} ]} without a "type" key. The builder filters with n.get("type") == "event", so every event is dropped and the bundle ends up with zero events ANALYSIS.
Fix plan: treat a missing "type" as event by default, or infer kind from the presence of from / to fields; remove the strict equality filter and expand the unit test to cover both shapes.
Root-Cause 3 – Cache never refreshes when snapshot_etag changes & thin-Redis signature wrong
Failing tests: test_etag_change_eviction
Suspect file: services/gateway/src/gateway/evidence.py
Explanation: (a) When the redis stub lacks .pipeline(), the fallback path calls setex(composite, ev.model_dump_json()) (missing ttl), so the stub's setex counter is not hit and the entry is not updated. (b) _is_fresh() uses the live ETag but, if the HTTP call fails, returns True (fail-open), silently serving stale evidence ANALYSIS.
Fix plan: always call setex(key, ttl, value), guard the fail-open branch with a feature flag, and move the freshness check before the early-return cache hit.
Root-Cause 4 – Fragile hasattr() logic in BM25 fallback search
Failing tests: test_resolver_stub, test_query_route_contract
Suspect file: services/gateway/src/gateway/resolver/fallback_search.py
Explanation: search_bm25() tries client.post only if hasattr(client,"post"); the unit-test stub omits that attribute, so the code falls back to client.request, which the stub also lacks, raising AttributeError and cascading into downstream failures ANALYSIS.
Fix plan: unconditionally call client.post() (present on real httpx.AsyncClient), update stubs, and drop the brittle branch.
Root-Cause 5 – Validator aliases the anchor and corrupts ID set logic
Failing tests: test_validator_subset_rule, test_validator_missing_anchor, test_orphan_event, test_no_transitions
Suspect file: packages/core_validator/src/core_validator/validator.py
Explanation: The validator injects _pretty_anchor(anchor.id) (alias "A1") into allowed_ids. Fixtures keep the full slug, so the subset check fails and the wrong error messages propagate ANALYSIS.
Fix plan: compare raw slugs, enforce supporting_ids ⊆ allowed_ids after checking mandatory anchor/transition citations, and align error strings with the spec.
Root-Cause 6 – Selector meta built before it recalculates allowed_ids
Failing tests: test_selector_truncates (second assertion)
Suspect file: services/gateway/src/gateway/selector.py
Explanation: In the prune branch, original_ids = set(ev.allowed_ids) is captured before the function calls _union_ids(ev), so dropped_evidence_ids is empty and selector_truncation may still be False.
Fix plan: recompute original_ids after the union or, simpler, move the union logic above the diff.
Root-Cause 7 – Schema-mirror fallback hard-codes "test-etag"
Failing tests: test_schema_mirror_fields_route
Suspect file: services/gateway/src/gateway/app.py
Explanation: When the upstream call fails, the fallback response sets x-snapshot-etag: test-etag; the fixtures expect "dummy-etag" ANALYSIS.
Fix plan: propagate the upstream header when present; otherwise return "dummy-etag" to match the contract.
Root-Cause 8 – Templater puts the alias ("A1") at the head of supporting_ids
Failing tests: test_templater_ask, test_templater_golden
Suspect file: services/gateway/src/gateway/templater.py
Explanation: _pretty_anchor() shortens slugs > 20 chars to "A1", and validate_and_fix() prepends that alias to supporting_ids. The schema and tests require the raw anchor slug to appear first ANALYSIS.
Fix plan: leave supporting_ids untouched except to prepend the raw anchor.id if missing; reserve aliasing for human-readable strings like short_answer.
Root-Cause 9 – Audit trail omits three mandatory artefacts
Failing tests: test_artifact_retention_comprehensive, test_artifact_metric_names
Suspect files: services/gateway/src/gateway/builder.py, services/gateway/src/gateway/app.py
Explanation: build_why_decision_response() writes only envelope, rendered_prompt, response, and validator_report. The spec (§ B5) and the tests expect seven artefacts, including evidence_pre.json, raw_llm.json, and plan.json. Missing artefacts break the MinIO stub assertions ANALYSIS.
Fix plan: capture the evidence bundle before and after truncation, persist the deterministic plan and the raw LLM output, increment the corresponding Prometheus counters, and add a regression test verifying all seven keys.