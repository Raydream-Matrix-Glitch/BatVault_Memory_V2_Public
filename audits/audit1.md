# Root Cause Analysis Report

## Root-Cause 1 – Audit/artefact pipeline skips templater-only requests

**Failing tests:** 
- test_full_artefact_retention
- test_gateway_audit_metadata_and_artefact_persistence
- test_gateway_schema_mirror_fields_route

**Suspect files:** 
- gateway/artifacts.py
- gateway/app.py
- gateway/schema_mirror.py

**Explanation:** 
ArtifactSink.write() early-returns when llm_mode=="off" (templater path). Consequently no artefacts are persisted, meta.snapshot_etag isn't injected into the HTTP payload, and the response header X-Snapshot-Etag falls back to a hard-coded "test-etag" instead of the Ingest-supplied "dummy-etag".

**Fix plan:**
- Remove the if llm_mode … return guard so artefacts are always written.
- Thread snapshot_etag from EvidenceBuilder → Response.meta and into all outgoing headers.
- Replace constant TEST_ETAG in schema_mirror.py with the live value returned by Memory-API.

---

## Root-Cause 2 – _collect_allowed_ids legacy shim mis-parses arguments

**Failing tests:** 
- test_neighbor_shape_normalisation
- test_backlink_derivation_contract

**Suspect files:** 
- services/gateway/src/gateway/evidence.py

**Explanation:** 
The two-argument compatibility path treats the second positional argument as pre instead of anchor; when callers pass (shape, anchor) the function tries anchor.id on a dict, raising AttributeError and returning an empty events list.

**Fix plan:**
- Refactor _collect_allowed_ids to detect the signature unambiguously (Union[Shape, Anchor]).
- Unit-test both call styles (old: 2-arg, new: 4-arg).
- Ensure allowed_ids is computed after all neighbor shapes are flattened.

---

## Root-Cause 3 – Cache key omits snapshot_etag; Redis wrapper API drift

**Failing tests:** 
- test_etag_change_eviction (plus noisy Redis warnings)

**Suspect files:** 
- gateway/evidence.py

**Explanation:** 
The composite cache key is (anchor_id,intent) – it ignores the current corpus version. A second request after the ETag flip reuses stale JSON, so ev2.snapshot_etag == ev1.snapshot_etag. The test's monkey-patched Redis also exposes a missing value arg in setex() and absent pipeline() handling.

**Fix plan:**
- Include snapshot_etag in the cache key or store it as a field and compare before returning.
- Update Redis helper to call setex(key, ttl, value) and guard pipeline() with hasattr.
- Add regression test: change ETag → expect cache miss.

---

## Root-Cause 4 – Resolver fallback & slug fast-path logic incorrect

**Failing tests:** 
- test_resolver_stub
- test_slug_fast_path
- test_router_query_route

**Suspect files:** 
- gateway/resolver/fallback_search.py
- gateway/resolver/__init__.py

**Explanation:**
- fallback_search() chooses client.request when no .post exists, but the stub client provides only .post, causing AttributeError.
- resolve_decision_text() still runs search even when the input string already matches the ID slug, so the wrong candidate is returned.

**Fix plan:**
- Call client.post() unconditionally (async or sync) – the live httpx client and the stub both expose it.
- Short-circuit in resolve_decision_text() when possible_id_slug(text) is true.
- Extend unit tests with mixed-case / underscore slugs.

---

## Root-Cause 5 – Selector/templater metadata out-of-spec

**Failing tests:** 
- test_selector_truncates
- test_templater_returns_contract

**Suspect files:** 
- gateway/evidence/selector_model.py
- gateway/templater.py

**Explanation:**
- _truncate_evidence() sets selector_truncation before items are dropped, so the flag is always False.
- The templater builds answer.supporting_ids from first evidence item instead of mandating anchor.id at index 0.

**Fix plan:**
- Calculate the flag after truncation (len(dropped_ids)>0).
- Pre-pend anchor.id to supporting_ids, de-dup the list, and add a test fixture.

---

## Root-Cause 6 – Validator rules drifted from updated spec

**Failing tests:** 
- test_validator_subset_rule
- test_validator_missing_anchor
- test_orphan_event
- test_no_transitions

**Suspect files:** 
- gateway/validator.py

**Explanation:** 
The spec (§ B4) now requires:
- supporting_ids ⊆ allowed_ids and must include anchor.id when present,
- completeness_flags.event_count == len(events),

but the implementation enforces the latter first, so the wrong error is emitted and some positives slip through.

**Fix plan:**
- Re-order checks: schema → mandatory IDs → subset → completeness flags.
- Update error codes to match spec (anchor.id missing, supporting_ids ⊈ allowed_ids, …).
- Add golden tests for orphan events and zero-transition decisions.