# Milestone 3 Implementation Review

## Component Analysis

### Evidence Builder
**Specification**: 15-minute two-key Redis cache with snapshot-etag invalidation, retry with jitter, returns fully-validated WhyDecisionEvidence.

**Implementation**: EvidenceBuilder in `services/gateway/evidence.py` implements Redis alias & composite keys (`CACHE_TTL_SEC = 900`), snapshot-etag plumbing, 1 retry with jitter.

**Issues**:
- No explicit cache-bust when `snapshot_etag` changes → alias can point to stale composite key
- HTTPX client is sync; spec requires p95 ≤ 3s → switch to AsyncClient + async FastAPI route to eliminate thread-blocking

### Selector
**Specification**: Deterministic baseline (recency + similarity), truncate at `SELECTOR_TRUNCATION_THRESHOLD`, emit selector logs (`selector_model_id`, `selector_truncation`, `dropped_ids`).

**Implementation**: `selector.py` computes ISO-timestamps, Jaccard similarity; logs via `log_stage("selector", meta)`; threshold constants in `core_config.constants`.

**Issues**:
- After truncation, `ev.allowed_ids` isn't recalculated, so IDs of dropped events remain allowed → downstream validator may pass although evidence was removed
- **Fix**: Add `allowed_ids = _compute_allowed_ids(ev)` post-truncate

### Prompt Envelope + Fingerprints
**Specification**: Canonical JSON; `prompt_fingerprint`, `bundle_fingerprint`; artifacts persisted to MinIO.

**Implementation**: `prompt_envelope.build_prompt_envelope` uses `canonical_json` + `_sha256`; `app.py` persists to bucket.

**Issues**:
- Bug in `_put`: uses undefined `mc.put_object`; should use the client instance

### Validator & Deterministic Fallback
**Specification**: Core-spec §11 rules enforced; if validator fails, deterministic fallback should patch answer and set `fallback_used = True`; errors stored in `validator_report.json`.

**Implementation**: `packages/core_validator/validator.py` checks schema, subset, anchor-in-support, completeness flags, etc. `gateway.app.ask()` calls `validate_response()` and tries `validate_and_fix`.

**Critical Bugs**:
1. `validate_and_fix` signature is `(answer, allowed_ids, anchor_id)` but called as `validate_and_fix(ev, ans)` and later `validate_and_fix(ev, resp.answer)` → raises TypeError, fallback path never repairs
2. `deterministic_short_answer` expects primitives but is called with `ev`; always throws, so templater never used

### Logging & Deterministic IDs
**Specification**: Structured JSON logs with `request_id`, `selector_model_id`, etc.

**Implementation**: `core_logging.JsonFormatter` OK. `ask()` generates `request_id`.

**Issues**:
- Decorator misuse: `@log_stage(logger,"gateway","ask")` placed above the route treats the plain logging helper as a decorator. `log_stage` returns `None`, so `router.post` receives `None` instead of the handler – FastAPI would raise at import. Tests pass only because the module isn't imported in them the same way.
- **Fix**: Replace with an inline call at the top of the function or create a proper decorator factory

### /v2/ask Route Contract
**Specification**: Must stream SSE or JSON (spec allows blocking JSON for M3); meta must not duplicate keys.

**Implementation**: JSON implemented.

**Issues**:
- Duplicate keys overwrite: `"gateway_version"` and `"sdk_version"` appear twice – second assignment wins, masking first
- Missing `/v2/query` stub for NL query path

### Load-shedding
**Specification**: Basic circuit-breaker returning 429 when unhealthy.

**Implementation**: `load_shed.should_load_shed()` placeholder returns `False`.

**Issues**:
- Not yet wired into `ask()`

### Tests
**Specification**: Golden suites for templater answers; validator edge cases; selector truncation; prompt fingerprint determinism.

**Implementation**: Unit tests exist for selector, validator negatives, prompt fingerprints, basic `/v2/ask`.

**Missing**:
- Golden templater tests (should assert deterministic answer text)
- Positive validator cases
- Integration test that exercises cache hit + etag invalidation
- Latency/performance tests

### Structured Boundaries & Misc
**Specification**: No cross-package circular dependencies; JSON-first models in `packages/*`; deterministic IDs everywhere.

**Issues**:
- `core_validator` imports `gateway.models` (downstream dependency) → violates modular boundary
- **Fix**: Place shared models in `packages/core_models`

## High-Priority Fixes (Blockers)

### 1. Replace Erroneous Decorator
```python
# services/gateway/src/gateway/app.py
@router.post("/v2/ask", response_model=WhyDecisionResponse)
async def ask(req: AskIn, ...):
    log_stage(logger, "gateway", "ask", request_id=request_id, intent=req.intent)
```

### 2. Correct MinIO Helper
```python
def _put(name: str, blob: bytes):
    client.put_object(...)   # not mc
```

### 3. Fix validate_and_fix & Templater Wiring
```python
answer, changed, errs = validate_and_fix(
    resp.answer, ev.allowed_ids, ev.anchor.id
)
```

And call `deterministic_short_answer` with primitives:
```python
short = deterministic_short_answer(
    ev.anchor.id, len(ev.events),
    len(ev.transitions.preceding), len(ev.transitions.succeeding),
    supporting_n=len(supporting), allowed_n=len(allowed)
)
```

### 4. Deduplicate Meta Keys
Add `selector_meta` before re-using field names.

### 5. Re-compute allowed_ids
After truncation and ensure validator re-run if evidence mutated.

**Note**: Each of these should be covered by unit + contract tests; add golden fixtures so that changing the templater or validator surfaces regressions instantly.

## Next-Step Action List (Ordered)

| # | Task | Strategic Logging Hook |
|---|------|------------------------|
| 1 | Apply blockers above; run `pytest -k 'gateway or validator' --ff` | `stage=ci_fix_m3_blockers` |
| 2 | Extract WhyDecision* Pydantic models to `packages/core_models`; update imports; enforce with import-cyclone linter | `stage=modular_boundary` |
| 3 | Implement async EvidenceBuilder with `httpx.AsyncClient`, propagate async through `ask()`; wrap latency budget with `@trace_span("ask")` | `stage=perf_async` |
| 4 | Complete load-shedding: compare redis latency + upstream 5xx ratio; return 429 Retry-After | `stage=load_shed` |
| 5 | Golden suites: templater output, validator positive/negative matrix, cache eviction on etag change | `stage=golden_tests` |
| 6 | Integration: docker-compose e2e covering /v2/ask→Gateway→Memory-API stub; assert p95 < 3s with 50 concurrent requests (pytest-benchmark) | `stage=e2e_perf` |
| 7 | Add contract & json-schema snapshot tests for prompt envelope (tech-spec §B) | `stage=contract_snapshot` |