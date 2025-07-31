🚨 1. Blockers
B-1: Evidence Cache Missing Write Operations
Problem: EvidenceBuilder.build() reads two-key scheme but never writes to Redis

Current Flow: Reads ALIAS_TPL → composite_key but returns immediately after logging
Impact: Every request is a cache miss, alias never updates on new snapshot_etags
Spec Violation: §H3 cache contract ("key = (decision_id, …, snapshot_etag)", TTL 15 min)
Fix Required: Write both keys after building bundle

B-2: Selector Meta Missing in Non-Truncation Branch
Problem: When no truncation occurs, selector.truncate_evidence() omits required fields

Missing Fields:

selector_model_id
bundle_size_bytes
max_prompt_bytes


Impact: Fields never reach evidence_built log
Spec Violation: §B5 requires these fields for every bundle log
Fix Required: Include all fields in both truncation and non-truncation branches

B-3: MIN_EVIDENCE_ITEMS Guarantee Broken
Problem: truncate_evidence() may drop all events if anchor alone fits threshold

Spec Requirement: M3 constant MIN_EVIDENCE_ITEMS = 1
Expected Behavior: Always keep anchor + ≥1 neighbor (decision/event/transition)
Current Behavior: Can return anchor-only bundles
Fix Required: Enforce minimum neighbor retention

B-4: Prompt Envelope Schema Drift
Problem: prompt_envelope.build_envelope() doesn't match spec structure

Current Output: "policy": "<string>" with omitted policy_id
Spec Requirement:

policy should be an object with retries/temperature
policy_id and prompt_id are first-class audit IDs


Fix Required: Restructure envelope to match spec exactly

B-5: Duplicate Policy Registry Sources
Problem: Two policy registries risk drift

Sources:

In-code _POLICY_REGISTRY
services/gateway/config/policy_registry.json


Risk: Hard-coded policies diverge from JSON config
Fix Required: Choose one source (JSON preferred) and remove the other

B-6: Unit Test Fixture Missing
Problem: Test expects file not in repository

Test: tests/test_validator.py
Expected File: fixtures/batvault_live_snapshot.tar.gz
Impact: CI will error on path-not-found
Fix Required: Commit missing fixture file

B-7: Cache Alias Pointer Write Missing
Problem: Even after B-1 fix, alias pointers won't update

Missing Operation: SETEX alias_key → composite_key
Current: Only writes SETEX composite_key → evidence_json
Impact: Old aliases never advance to new ETag
Fix Required: Write both composite key and alias pointer

B-8: Selector Retries Counter Incorrect
Problem: meta.retries hard-wired to 0 despite evidence builder performing retries

Current: Always reports 0 retries
Actual: Evidence builder already performs one retry
Impact: Downstream analytics are blind to retry behavior
Fix Required: Increment counter to reflect actual retry attempts


🔧 2. Follow-ups / Polish
P-1: Envelope → MinIO Enhancement
Current: Envelope persisted to f"{request_id}/envelope.json" ✅
Suggestion: Add rendered prompt & validator report in same helper for complete audit trail
P-2: SDK / Gateway Versioning
Problem: sdk_version="1.0.0" is hard-coded
Risk: Stale values in production
Solution: Pull from pkg_resources or pyproject.toml dynamically
P-3: Constants Consolidation
Current: Introduced core_config.constants ✅
Issue: Selector still defines local constants (redundant)

MAX_PROMPT_BYTES
SELECTOR_TRUNCATION_THRESHOLD
Solution: Ensure all services import shared values

P-4: Logging Parity
Issue: Non-truncation meta lacks dropped_evidence_ids = []
Impact: Inconsistent logging structure between branches
Solution: Include empty array for parity, keeps dashboards simple
P-5: Type Hints & Documentation
Missing: Return-type hints on helper functions

minio_client
bundle_size_bytes
Impact: CI mypy job will complain under --strict
Solution: Add proper type annotations and docstrings