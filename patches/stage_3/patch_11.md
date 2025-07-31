# Implementation Changes Documentation

## 1. Extract & Centralize Pydantic Models

### Added
- **`packages/core_models/src/core_models/models.py`**
  - All `WhyDecision*` classes (Anchor, Evidence, Answer, Transitions, Flags, Response)
  - New `PromptEnvelope` model for NL-routing contracts

### Deleted
- **`services/gateway/src/gateway/models.py`**

### Updated Imports
- **Gateway** (`app.py`) now imports schemas from `core_models.models`
- **Validator** (`packages/core_validator/src/core_validator/validator.py`) likewise

## 2. `/v2/ask` Handler & Core Logic in `app.py`

### Route Changes
- `@router.post("/ask")` → `@router.post("/v2/ask")`
- Removed the old `@log_stage` decorator, inlined `log_stage(...)` at function start
- Added `@trace_span("ask")` for end-to-end latency spans

### Core Updates
- **Async EvidenceBuilder**: switched from sync to `await _evidence_builder.build(...)`
- **MinIO helper**: refactored duplicate `_put` calls into a single `_minio_put_batch()` utility
- **Validator fix**: unpacked `(answer, changed, errs) = validate_and_fix(...)` correctly, and surfaced `meta["fallback_used"]` & `meta["validator_errors"]`
- **Meta cleanup**: namespaced selector metadata under `"selector_meta"`; deduped repeated version fields
- **Load-shedding gate**: early `if should_load_shed(): return 429 Retry-After` with `meta.load_shed = True`
- **NL-routing stub** (`/v2/query`) was already present and untouched

## 3. Asynchronous EvidenceBuilder in `evidence.py`

### Conversions
- **Converted** `build(anchor_id)` → `async def build(anchor_id)`
- **HTTP fetch** via `httpx.AsyncClient` + `await` (with jittered retry)
- **Redis cache** keyed on `snapshot_etag` (via `r.get` / `r.setex`)
- **Pydantic instantiation** of `WhyDecisionEvidence` and metadata logging

## 4. Real Load-Shedding in `load_shed.py`

### Implementation
**Implemented** `should_load_shed()` to:
- Ping Redis and measure latency vs. `load_shed_redis_threshold_ms`
- Hit `GET /healthz` on Memory-API and circuit-break on 5xx or errors

## 5. "Golden" Test Suites

### Templater
- **`services/gateway/tests/test_templater_golden.py`**
  - Unit tests for `deterministic_short_answer(...)` and `validate_and_fix(...)`

### Validator Matrix
- **`packages/core_validator/src/core_validator/tests/test_validator_golden_matrix.py`**
  - Parametrized edge-case matrix driving `validate_response(...)`

### EvidenceBuilder Cache
- **`services/gateway/tests/test_evidence_builder_cache.py`**
  - Integration-style async tests that load your real JSON fixtures from `memory/fixtures`
  - Stub out HTTPX, and assert cache-hit vs. etag-change behavior

### Contract Snapshot
- **Committed** `packages/core_models/src/core_models/schemas/prompt_envelope.schema.json` (the "golden" JSON-Schema for `PromptEnvelope`)
- **Test** `services/gateway/tests/test_prompt_envelope_contract.py` which regenerates `PromptEnvelope.schema()` and strictly compares it to the committed snapshot