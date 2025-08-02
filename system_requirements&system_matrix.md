# System Requirements & Testing Matrix

## Milestone Requirements (M-Series)

### M1: Tech Stack & Quick Setup ✅
**Description:** Tech stack & "up in <5 min"
- **Code Paths:** `docker-compose.yml`, `Dockerfile.*` in root, `scripts/smoke.sh`
- **Test Paths:** `scripts/smoke.sh`
- **Status:** Complete

### M2: Performance Requirements

#### M2.1: Ask Endpoint Performance ⚠️
**Description:** `/v2/ask` p95 ≤ 3,000 ms
- **Code Paths:** `services/gateway/src/gateway/app.py` (ask endpoint)
- **Test Paths:** No latency-measurement test
- **Status:** Missing tests

#### M2.2: Query Endpoint Performance ⚠️
**Description:** `/v2/query` p95 ≤ 4,500 ms
- **Code Paths:** 
  - `services/api_edge/src/api_edge/app.py` (v2_query_passthrough)
  - `services/gateway/src/gateway/app.py` (@app.post("/v2/query"))
- **Test Paths:** No latency-measurement test
- **Status:** Missing tests

#### M2.3: Model Inference Speed ⚠️
**Description:** Model inference speed (resolver ≤ 5 ms; selector ≤ 2 ms)
- **Code Paths:** 
  - `services/gateway/src/gateway/resolver/…`
  - `services/gateway/src/gateway/selector.py`
- **Test Paths:** No micro-benchmark/speed tests
- **Status:** Missing tests

### M3: Error Handling & Validation

#### M3.1: LLM JSON Fallback ⚠️
**Description:** Invalid LLM JSON → deterministic fallback (fallback_used=true)
- **Code Paths:** `services/gateway/src/gateway/validator.py` (validate_and_fix)
- **Test Paths:** No explicit "invalid JSON → fallback" test in gateway
- **Status:** Missing tests

#### M3.2: Evidence Truncation Logging ✅
**Description:** Evidence truncation logs (selector_truncation, dropped_evidence_ids)
- **Code Paths:** `services/gateway/src/gateway/selector.py` (truncate_evidence)
- **Test Paths:** `tests/unit/services/gateway/test_selector_edge_cases.py`
- **Status:** Complete

### M5: Audit Metadata ⚠️
**Description:** Audit metadata in every response: prompt_id, policy_id, prompt_fingerprint, snapshot_etag; artifacts
- **Code Paths:** 
  - `packages/core_logging/` instrumentation used in:
    - `services/api_edge/src/api_edge/app.py`
    - `services/gateway/src/gateway/app.py`
    - `services/memory_api/src/memory_api/app.py`
- **Test Paths:** No end-to-end test verifying full audit envelope & persistence (only schema-mirror header tested)
- **Status:** Missing comprehensive tests

### M6: Schema-Agnostic Proof ✅
**Description:** Schema-agnostic proof: `/v2/schema/fields` & `/v2/schema/rels` mirror
- **Code Paths:** `services/gateway/src/gateway/app.py` (schema_mirror routes)
- **Test Paths:** `tests/unit/services/gateway/test_gateway_schema_mirror.py`
- **Status:** Complete

### M7: Golden Tests ✅
**Description:** Golden tests pass (coverage=1.0, completeness_debt=0)
- **Code Paths:**
  - `packages/core_validator/src/core_validator/tests/test_validator_golden_matrix.py`
  - `services/gateway/tests/test_templater_golden.py`
  - `services/ingest/tests/golden/*.json`
- **Test Paths:** 
  - `tests/unit/packages/core_validator/test_validator_golden_matrix.py`
  - `tests/unit/services/gateway/test_templater_golden.py`
  - `tests/unit/services/ingest/golden/*.json`
- **Status:** Complete

## Reliability Requirements (R-Series)

### R2.1: Health & Readiness Endpoints ⚠️
**Description:** Health & readiness endpoints on all services (incl. ArangoDB for memory-api)
- **Code Paths:**
  - `services/api_edge/src/api_edge/app.py:healthz`
  - `services/gateway/src/gateway/app.py:healthz`
  - `services/memory_api/src/memory_api/app.py:healthz`
  - No healthz in `services/ingest`
- **Test Paths:**
  - `tests/unit/services/api_edge/test_api_edge_health.py`
  - `tests/unit/services/gateway/test_gateway_health.py`
  - `tests/unit/services/memory_api/test_memory_api_health.py`
- **Status:** Missing ingest service health endpoint

### R2.2: Quality Gates ⚠️
**Description:** Quality gates (golden tests, audit trail, schema-agnostic, orphan handling)
- **Code Paths:** Covered under M7, M5, M6, P.8
- **Test Paths:** Covered under those same suites
- **Status:** Dependent on other requirements

### R3: Performance & Reliability SLOs

#### R3.1: Performance SLOs ⚠️
**Description:** Performance SLOs (same as M2.1/M2.2)
- **Code Paths:** See M2.1 & M2.2
- **Test Paths:** See M2 tests
- **Status:** Same as M2 requirements

#### R3.2: Fallback Rate Reliability ⚠️
**Description:** Reliability: fallback_used < 5% under load metrics
- **Code Paths:** Metrics hooked via `packages/core_telemetry/` (no code in snapshot?)
- **Test Paths:** No integration test for fallback rate
- **Status:** Missing telemetry and tests

#### R3.3: Artifact Retention ⚠️
**Description:** Auditability: 100% artifact retention
- **Code Paths:** Artifact persistence in `services/api_edge/.../persistence.py?`, `services/gateway/.../persistence.py?` (only partial)
- **Test Paths:** No test verifying storage of all artifacts
- **Status:** Missing comprehensive artifact storage tests

#### R3.4: Development Experience ✅
**Description:** Dev experience: docker-compose up < 5 min + MinIO bucket check
- **Code Paths:** `docker-compose.yml` + `scripts/smoke.sh`
- **Test Paths:** `scripts/smoke.sh`
- **Status:** Complete

### R4: Testing Requirements

#### R4.a: New Golden Test Cases ✅
**Description:** New golden test cases
- **Code Paths:** See M7
- **Test Paths:** See M7
- **Status:** Complete

#### R4.b: New Validator Unit Tests ✅
**Description:** New validator unit tests
- **Code Paths:** `packages/core_validator/src/core_validator/tests/`
- **Test Paths:** `tests/unit/packages/core_validator/test_validator_golden_matrix.py`
- **Status:** Complete

#### R4.c: Router Contract Tests ⚠️
**Description:** Router contract tests (`/v2/ask`, `/v2/query`, back-link derivations)
- **Code Paths:** `services/gateway/src/gateway/app.py` (implements routes)
- **Test Paths:** Only `tests/unit/services/gateway/test_templater_ask.py` covers `/v2/ask`; no contract test for `/v2/query` or back-links
- **Status:** Missing `/v2/query` and back-link tests

## Product Requirements (P-Series)

### P.1: Snapshot ETag Propagation ⚠️
**Description:** Snapshot-ETag in ingest & schema endpoints; propagate headers
- **Code Paths:**
  - Ingest: `services/ingest/src/ingest/watcher.py` (should tag snapshots)
  - Memory-API: `services/memory_api/src/memory_api/app.py`
  - Gateway: schema mirror already sets header
- **Test Paths:** Only `tests/unit/services/gateway/test_gateway_schema_mirror.py` and `tests/unit/services/memory_api/test_schema_http_headers.py` verify ETag on schema routes
- **Status:** Missing ingest ETag implementation and tests

### P.2: ISO-8601 UTC Timestamps ✅
**Description:** Timestamps as ISO-8601 UTC (Z)
- **Code Paths:** `services/ingest/src/ingest/cli.py` (TS_RE)
- **Test Paths:** `tests/unit/services/ingest/test_strict_id_timestamp.py`
- **Status:** Complete

### P.3: Event Summary Repair ✅
**Description:** Event summary repair + snippet from description
- **Code Paths:** `services/ingest/src/ingest/pipeline/snippet_enricher.py`
- **Test Paths:**
  - `tests/unit/services/ingest/test_snippet_enricher.py`
  - `tests/unit/services/ingest/test_snippet_golden.py`
- **Status:** Complete

### P.4: Cross-Link Reciprocity ⚠️
**Description:** Cross-link reciprocity (led_to⇄supported_by, based_on⇄transitions)
- **Code Paths:** `services/ingest/src/ingest/pipeline/normalize.py`
- **Test Paths:** No test explicitly asserting bidirectional links
- **Status:** Missing reciprocity tests

### P.5: Catalog Endpoints ✅
**Description:** Catalog endpoints under `/api/schema/*`, mirrored at `/v2/schema/*`
- **Code Paths:**
  - Memory-API: `services/memory_api/src/memory_api/app.py`
  - Gateway mirror: `services/gateway/src/gateway/app.py`
- **Test Paths:**
  - `tests/unit/services/memory_api/test_schema_http_headers.py`
  - `tests/unit/services/gateway/test_gateway_schema_mirror.py`
- **Status:** Complete

### P.6: k=1 Expansion + Truncation ✅
**Description:** k=1 expansion + truncation
- **Code Paths:** `services/memory_api/src/memory_api/expand_candidates.py`
- **Test Paths:** `tests/unit/services/memory_api/test_expand_candidates_unit.py`
- **Status:** Complete

### P.7: Structured Logging ⚠️
**Description:** Structured logs with snapshot_etag at every stage
- **Code Paths:** `packages/core_logging/`, wired into all services
- **Test Paths:** No test validating presence of snapshot_etag in all structured logs
- **Status:** Missing comprehensive logging tests

### P.8: Orphan Handling ✅
**Description:** Orphan handling (events without led_to, decisions without transitions)
- **Code Paths:** `services/ingest/src/ingest/pipeline/normalize.py`
- **Test Paths:** `tests/unit/services/ingest/test_contract_orphans.py`
- **Status:** Complete

### P.9: New Field Support ⚠️
**Description:** New field support (tags, based_on, snippet, x-extra)
- **Code Paths:** `services/ingest/src/ingest/pipeline/normalize.py`, `services/ingest/src/ingest/cli.py`
- **Test Paths:** No tests covering tags or based_on normalization
- **Status:** Missing field normalization tests

### P.10: Empty Link Arrays ⚠️
**Description:** Empty link arrays default to []
- **Code Paths:** `services/ingest/src/ingest/pipeline/normalize.py`
- **Test Paths:** No tests for ensuring omitted link arrays become []
- **Status:** Missing default value tests

## Quality Requirements (Q-Series)


### Q.2: Auth & CORS at API Edge ⚠️
**Description:** Auth & CORS at API edge
- **Code Paths:** `services/api_edge/src/api_edge/app.py` (rate limiting via SlowAPI), No CORS middleware present
- **Test Paths:** No tests for API-key auth or CORS headers
- **Status:** Missing CORS implementation and auth/CORS tests

## Summary

**Complete Requirements:** 9/24 (37.5%)
- ✅ M1, M3.2, M6, M7, R3.4, R4.a, R4.b, P.2, P.3, P.5, P.6, P.8

**Incomplete Requirements:** 15/24 (62.5%)
- ⚠️ M2.1, M2.2, M2.3, M3.1, M5, R2.1, R2.2, R3.1, R3.2, R3.3, R4.c, P.1, P.4, P.7, P.9, P.10, Q.1, Q.2

**Key Missing Areas:**
- Performance testing and monitoring
- Comprehensive audit trail testing
- Authentication and CORS implementation
- Cross-link reciprocity validation
- Field normalization testing





# Test Coverage Summary - Update

## Well-Covered Areas ✅

### **Ingest & Catalog**
All contracts, aliasing, backlinking, snippet enrichments.

### **Resolver & Expansion**
Unit- and contract-level tests for AQL, caching, timeouts.

### **Evidence Bundling & Selector**
Cache TTL, truncation logic, learned-scorer behavior.

### **Fingerprinting & Prompt Determinism**
JSON-envelope structure & fingerprint uniqueness.

### **Artifact Retention**
Full artefact set in MinIO stub; ETag logging.

## Partially Covered Areas ⚠️

### **LLM Integration**
- ✅ **Covered:** Fallback path and `fallback_used` flag
- ❌ **Missing:** Explicit "retry twice then fallback" behavior isn't asserted

### **Validation & Streaming**
- ✅ **Covered:** Schema & edge-case coverage
- ❌ **Missing:** No end-to-end SSE streaming integration tests asserting streamed chunks or meta flags (`fallback_used`, `load_shed`)

## Not Covered / Missing Areas ❌

### 1. **Retry Logic**
Ensure LLM call retries exactly twice on JSON/schema errors before templater fallback.

### 2. **SSE Streaming**
Full integration tests for Server-Sent-Events: buffering, chunk boundaries, and meta flags in the stream.

### 3. **Observability & Metrics**
Presence and correctness of metrics (cache hits, selector truncation counts, total neighbors found) and OpenTelemetry spans.

### 4. **S3/MinIO Integration**
While stubbed retention is tested, consider an integration test against a real S3 mock to validate content-addressable writes and ETag propagation in HTTP headers.

---

## Testing Priority Recommendations

**High Priority:**
- Retry logic testing (critical for reliability)
- SSE streaming integration tests (user-facing feature)

**Medium Priority:**
- Observability metrics validation (operational visibility)
- S3/MinIO integration testing (data integrity)

**Notes:**
- Well-covered areas provide solid foundation for core functionality
- Partially covered areas need completion to ensure robust production behavior
- Missing areas represent gaps that could impact system reliability and observability