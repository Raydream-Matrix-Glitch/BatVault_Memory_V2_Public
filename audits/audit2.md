# Root Cause Analysis Report

## Root-Cause 1 – Snapshot ETag not threaded end-to-end

**Failing tests:**
- tests/unit/gateway/test_etag_change_eviction
- …/gateway/test_gateway_audit_metadata
- …/gateway/test_gateway_schema_mirror

**Suspect files:**
- services/gateway/src/gateway/evidence.py
- services/gateway/src/gateway/app.py
- services/gateway/src/gateway/schema_mirror.py

**Explanation:**
EvidenceBuilder writes the cache key without snapshot_etag, so on the second call it hits Redis and re-emits the stale bundle (etag1). Because the etag never surfaces again, downstream helpers (_audit_meta, schema-mirror route) fall back to a default "test-etag" constant, breaking the contract and headers.

**Fix plan:**
- Include snapshot_etag in the Redis key and in the cached value.
- On every Memory-API call, read X-Snapshot-Etag and attach it to WhyDecisionEvidence.snapshot_etag and resp.meta["snapshot_etag"].
- Forward that header transparently in GET /v2/schema/* mirror routes.
- Update/extend unit tests to assert cache-key churn on etag change.

---

## Root-Cause 2 – _collect_allowed_ids legacy-API shim breaks new call‐site

**Failing tests:**
- …/gateway/test_neighbor_contract_shapes

**Suspect files:**
- services/gateway/src/gateway/evidence.py

**Explanation:**
The helper tries to distinguish the old _collect_allowed_ids(shape, anchor) call from the new 4-arg form by checking events is None. When tests pass (shape, anchor) the second positional argument lands in events, so the branch that expects anchor actually receives the dict shape ⇒ AttributeError: 'dict' object has no attribute 'id'.

**Fix plan:**
- Replace the positional-magic heuristic with an explicit signature: _collect_allowed_ids(shape: dict, anchor: WhyDecisionAnchor, *, events=None, pre=None, suc=None) and deprecate the legacy form.
- Add unit test for both flat-list and namespaced neighbor shapes.

---

## Root-Cause 3 – Resolver fallback & slug-fast-path logic inverted

**Failing tests:**
- …/gateway/test_resolver_stub
- …/gateway/test_slug_fast_path
- …/gateway/test_router_query

**Suspect files:**
- services/gateway/src/gateway/resolver/fallback_search.py
- services/gateway/src/gateway/resolver/__init__.py

**Explanation:**
search_bm25 tests if hasattr(client,"post") (always true for httpx.AsyncClient) and therefore never executes the stub's post method. In stubbed mode the else-branch is needed, so the code raises '_DummyClient' has no attribute 'request'. Separately, the slug "fast path" is missing: any text that already matches the decision-id regex still goes through BM25 search and returns the first fixture (panasonic-exit-plasma-2012), not the requested slug.

**Fix plan:**
- Drop the brittle hasattr guard – always call client.post.
- Implement is_slug(text) (re.fullmatch(SLUG_REGEX, text)) and short-circuit resolve_decision_text when true.
- Cache slug hits with the normal resolver key to keep the 5-minute TTL semantics.

---

## Root-Cause 4 – Audit-artifact sink only stores envelope

**Failing tests:**
- …/gateway/test_full_artefact_retention

**Suspect files:**
- services/gateway/src/gateway/artifact_sink.py
- request pipeline hooks

**Explanation:**
The pipeline writes envelope.json but never emits rendered_prompt.txt, llm_raw.json, response.json, pre/post evidence, or validator_report.json. The MinIO spy therefore reports all seven suffixes missing.

**Fix plan:**
Hook sinks at three points:
- a. before validation – dump evidence_pre.json & rendered_prompt.txt
- b. immediately after LLM – dump llm_raw.json
- c. after validation – dump evidence_post.json, validator_report.json, response.json

Ensure request_id/ prefix and deterministic filenames match spec.

---

## Root-Cause 5 – Selector truncation flag never flips

**Failing tests:**
- …/gateway/test_selector_truncates

**Suspect files:**
- services/gateway/src/gateway/selector.py
- evidence.py

**Explanation:**
truncate_evidence returns both (ev, meta). It correctly drops items but always sets meta["selector_truncation"]=False because flag assignment happens before the length check.

**Fix plan:**
Move flag assignment after truncation loop and add assertion in unit tests that dropped_evidence_ids is non-empty only when flag is true.

---

## Root-Cause 6 – Validator rule & message drift

**Failing tests:**
- …/gateway/test_validator_subset_rule
- …/gateway/test_validator_missing_anchor
- …/gateway/test_validator_edgecases (2 cases)

**Suspect files:**
- packages/core_validator/src/validator.py

**Explanation:**
Recent spec tightened rules (supporting_ids ⊆ allowed_ids, mandatory anchor citation, completeness-flag consistency). Validator still returns early on the first mismatch and its error strings no longer match the contract. This flips pass/fail expectations for subset and completeness cases.

**Fix plan:**
- Refactor to evaluate all checks and accumulate messages.
- Update messages exactly per tech-spec §F4 (e.g. anchor.id missing, supporting_ids ⊈ allowed_ids, completeness_flags.event_count mismatch).
- Add edge-case tests with orphan events and zero-transition decisions.

---

## Root-Cause 7 – Templater mis-orders supporting_ids

**Failing tests:**
- …/gateway/test_templater_ask

**Suspect files:**
- services/gateway/src/gateway/templater.py

**Explanation:**
The deterministic template inserts evidence IDs in discovery order; when selector returns [A1,…] first, that becomes supporting_ids[0]. Contract mandates anchor first.

**Fix plan:**
Prefix the anchor ID always, then append sorted supporting IDs; keep dedupe logic.

---

## Self-review checklist

- Every failing test is linked to at least one root-cause.
- Each root-cause lists concrete suspect files and a focused fix.
- Cross-cutting issues (ETag propagation, cache keys) are not duplicated.
- Validator & selector fixes reference the normative spec sections.
- No contradictions or gaps found.

**END OF ANALYSIS**