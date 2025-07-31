# Evidence-Bundle Pipeline Status Report

## 1. Evidence-bundle Pipeline (services/gateway)

| Area | Spec Requirement | Implementation Status | Issues / Action Items |
|------|------------------|----------------------|----------------------|
| **Full k = 1 collect, then truncate only when len(bundle) > MAX_PROMPT_BYTES** | Tech-Spec §B2 & checklist §E/Evidence Selection | ✅ EvidenceBuilder.build() gathers neighbours and hands the bundle to truncate_evidence() | **Critical Issue**: The selector stops after the very first time the bundle dips below the truncation threshold: `for e in events_sorted: … if bundle_size <= THRESHOLD: break` (line 57). Result: we keep one event in 90% of cases. **Fix**: keep adding while the next addition keeps us under the hard limit, or collect-then-drop. |
| **Deterministic baseline scorer = recency + similarity** | Milestone 3 bullet 1 | ❌ selector.py defines _sim() and _score() but has bugs | 1) _sim() references item & anchor that aren't in scope<br>2) _score() returns (timestamp, 0.0) ⇒ similarity ignored<br>3) duplicate imports & constants |
| **Selector metadata logging** | Spec §B2 / checklist §G ('selector_truncation' block) | ⚠️ Basic dict is returned ✅ | But we never pass meta on to the trace-logger (core_logging.log_stage) or expose it in WhyDecisionResponse.meta. Missing fields: total_neighbors_found, bundle_size_bytes, etc. |
| **Constants single-source** | Checklist §E | ❌ Hard-coded values | selector.py hard-codes MAX_PROMPT_BYTES = 8192 instead of importing from core_config.constants (already present). Ditto for SELECTOR_TRUNCATION_THRESHOLD. Risk of drift. |

## 2. Prompt-envelope Builder (gateway/prompt_envelope.py)

| Issue | Impact / Spec Reference | Recommendation |
|-------|------------------------|----------------|
| **File system dependency at import** | Reads policy_registry.json at import time → file-system failure will explode the worker process | **Availability Risk**: Wrap in lazy loader / cached property with explicit error if registry missing. |
| **Missing envelope fields** | Envelope fields are fine, but we omit snapshot_etag, bundle_fingerprint, and prompt_fingerprint in the JSON we later store → violates §8.1/§C | **Audit trail failure**: Add them before persistence; you already compute bundle_fp—just store it and pass down the line. |
| **Schema inconsistency** | explanations currently a list, spec says object with named keys | **Schema drift**: Change to Dict[str,str] or update JSON-schema + tests. |

## 3. Core Validator (packages/core_validator/validator.py)

| Check | Spec | Status |
|-------|------|--------|
| supporting_ids ⊆ allowed_ids | ✅ implemented | ✅ |
| anchor.id must be cited | ✅ implemented | ✅ |
| Transition IDs present ⇒ must be cited | ✅ implemented | ✅ |
| **Schema check of entire WhyDecisionResponse** | Spec §B4 & checklist §F | ❌ Missing – we only validate the answer sub-model. |
| **Completeness flags validation** | Spec §F1 | ❌ Enforced presence of completeness_flags.event_count = len(events) not validated. |
| **Fallback on validation failure** | Gateway integration | ❌ Gateway's app.py does not call validator yet. Wire it into ask_handler after LLM/templater call; on failure set meta.fallback_used = true, run templater. |

## 4. Gateway FastAPI App & Artifact Retention

### Current Status
- **Artifacts**: gateway.app writes prompt envelope & raw LLM JSON to MinIO, but skips the selector meta & validator report
- **Schema mirror route**: passes tests, but no cache invalidation on snapshot_etag header yet (Milestone 2 requirement)
- **Load-shedding**: flag (meta.load_shed) not implemented – harmless for M3 but required by checklist §H

## 5. Tests & Coverage Gaps

| Missing / Weak Test | Spec Reference |
|-------------------|----------------|
| **Golden fixtures** | why_decision_orphan_event.json, why_decision_standalone.json, etc. (Checklist §I) |
| **Selector edge cases** | No test exercises similarity ranking or truncation edge when exactly 8192 bytes |
| **Validator negative cases** | Need cases for supporting_ids ⊄ allowed_ids with transitions present |
| **Prompt builder determinism** | Deterministic fingerprint reproducibility test |
| **Evidence builder caching** | Cache alias expiry (TTL = 15 min) behaviour |

## 6. Minor Inconsistencies & Tidy-ups

- **Code quality**: Duplicated imports and unused symbols in selector.py produce flake8 warnings
- **Timeouts**: HTTP timeouts in EvidenceBuilder: 3s > 250ms stage budget (§H2). Use httpx.Timeout(0.25)
- **Documentation**: ALIAS_TPL comment says "alias → composite key" but Redis setex uses it as the actual key (prefixed). Document or rename
- **Model immutability**: WhyDecisionEvidence.allowed_ids is filled after model instantiation; nicer to compute first and pass in constructor
- **Deterministic output**: core_utils.fingerprints.canonical_json() should set orjson.OPT_SORT_KEYS|OPT_OMIT_MICROSECONDS to ensure deterministic output for identical timestamps

## 7. Milestone 3 "Definition of Done" Blockers

### Critical Path Items
1. **Fix selector scoring & loop logic** – otherwise evidence bundles are undersized and answers will lack context
2. **Add full-response schema & ID-scope validation** – wire validator into request flow, produce fallback on failure
3. **Persist complete artifact set** – selector meta, validator report, and include prompt_fingerprint, bundle_fingerprint, snapshot_etag in meta
4. **Infrastructure completeness** – implement or stub load-shedding flag, cache invalidation on snapshot_etag, and stage time-outs
5. **Test coverage** – bring the missing golden & edge-case tests online; raise coverage back to 1.0

---

*Status: Milestone 3 blocked on critical selector logic fixes and validation integration*