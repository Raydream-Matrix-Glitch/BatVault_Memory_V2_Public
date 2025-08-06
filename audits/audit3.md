# Root Cause Analysis Report

## Root-Cause 1 – allowed_ids/validation flow diverges from spec

**Failing tests:**
- test_backlink_derivation_contract
- test_neighbor_shape_normalisation
- test_validator_subset_rule
- test_validator_missing_anchor
- test_orphan_event
- test_no_transitions

**Suspect files:**
- services/gateway/src/gateway/evidence.py (_collect_allowed_ids)
- services/gateway/src/gateway/validator.py

**Explanation:**
The shim in _collect_allowed_ids tries to support the legacy 2-arg signature, but mis-detects the call shape. When events is None it treats the anchor argument as pre, so anchor.id is lost and the returned set is wrong. Consequently allowed_ids is incomplete, validators mis-fire, and backlink tests see zero events. The validator then enforces equality instead of the intended subset rule, flipping several pass/fail expectations.

**Fix plan:**
- Rewrite _collect_allowed_ids(*, shape, anchor, events=None, pre=None, suc=None) to use explicit keywords, always seed {anchor.id} first, and unit-test both call styles.
- Update validator logic: check supporting_ids ⊆ allowed_ids (not equality) and run mandatory-anchor check before subset check.
- Regenerate validator error messages to match spec (anchor.id missing, supporting_ids ⊈ allowed_ids, etc.).

---

## Root-Cause 2 – Redis write path & snapshot_etag propagation broken

**Failing tests:**
- test_etag_change_eviction
- test_gateway_audit_metadata
- test_gateway_schema_mirror

**Suspect files:**
- services/gateway/src/gateway/evidence.py (EvidenceBuilder.build)
- services/gateway/src/gateway/app.py (schema mirror route)

**Explanation:**
EvidenceBuilder assumes redis.pipeline() exists; when patched with a simple mock it falls back to .setex but calls it with the wrong arity (setex(key, ttl, value) → we pass only (key,value)). The exception aborts the second cache write, so the stale bundle (etag1) is reused and snapshot_etag never reaches the response/meta. The schema-mirror route hard-codes "test-etag" instead of relaying the Memory-API header expected by the stub ("dummy-etag").

**Fix plan:**
- Add safe fallback: if pipeline() missing, call setex(key, ttl, value) correctly.
- Always copy snapshot_etag from Memory-API headers into both response headers and meta.
- Drive the mirror route from the cached field-catalog response rather than a constant.
- Extend unit tests with a minimal MockRedis that asserts the correct setex signature.

---

## Root-Cause 3 – Artifact writer skipped on deterministic (templater) path

**Failing tests:**
- test_full_artefact_retention
- part of test_gateway_audit_metadata

**Suspect files:**
- services/gateway/src/gateway/artifacts.py
- services/gateway/src/gateway/app.py

**Explanation:**
The artifact sink is only invoked inside the LLM branch. When the pipeline returns via the templater (which all unit stubs do), no artefacts are pushed, leaving _dummy_minio.put_calls empty.

**Fix plan:**
- Move ArtifactWriter.persist() to a finally-block that runs for every successful /v2/ask execution.
- Generate the full artefact list (envelope.json, evidence_pre.json, …) even when llm_mode = off.
- Unit-test: call gateway with templater and assert seven artefacts exist.

---

## Root-Cause 4 – Resolver slug short-circuit & HTTP-stub incompatibility

**Failing tests:**
- test_resolver_stub
- test_slug_fast_path
- test_router_query_route_contract

**Suspect files:**
- services/gateway/src/gateway/resolver/__init__.py
- services/gateway/src/gateway/resolver/fallback_search.py

**Explanation:**
- **Slug path** – resolve_decision_text() only short-circuits when the slug is already in the graph cache. The spec requires bypassing search whenever the text matches the slug regex, even if unseen; this returns the wrong anchor for "foo-bar-2020".
- **HTTP path** – fallback_search chooses client.request() when hasattr(client,"post")==False; the stubbed _DummyClient lacks request, raising AttributeError.

**Fix plan:**
- Treat any text that matches ^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$ as a slug and return it directly with confidence=1.0.
- Simplify fallback: always call client.post(...) (every real/ test client implements it).
- Provide a shim post method on _DummyClient to avoid future drift.

---

## Root-Cause 5 – Selector-truncation flag & templater ID mapping

**Failing tests:**
- test_selector_truncates
- test_templater_returns_contract

**Suspect files:**
- services/gateway/src/gateway/selector.py
- services/gateway/src/gateway/templater.py

**Explanation:**
The selector sets selector_truncation=false unless all items were dropped; spec says flag must be true whenever any evidence is discarded. Separately, the templater emits placeholder IDs ("A1","A2",…) instead of the anchor when building supporting_ids.

**Fix plan:**
- Flip the truncation condition: meta["selector_truncation"] = dropped_count > 0.
- In templater.answer(), inject anchor.id as the first element of supporting_ids, followed by the top-ranked evidence IDs.
- Add sanity-check unit test for templater output schema.