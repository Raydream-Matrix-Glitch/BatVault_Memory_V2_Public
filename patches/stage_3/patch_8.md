# Milestone 3 Status Report - BatVault Live Snapshot

## High-Level Verdict

| Area | Spec / Requirement | State in batvault_live_snapshot | Gaps / Issues |
|------|-------------------|--------------------------------|---------------|
| **Evidence selector** | Must rank by recency + similarity and log full selector-meta | selector.py still returns only the recency score and hard-codes MAX_PROMPT_BYTES; Jaccard helper is unused and shadows imports. Logs omit total_neighbors_found & selector_model_id. | Implement real similarity term; remove duplicate constants; emit complete meta block. |
| **Prompt envelope & fingerprints** | Canonical JSON → prompt_fingerprint, bundle hash, registry-driven IDs | prompt_envelope.build_envelope() assembles envelope & calls core_utils.fingerprints.prompt_fingerprint (good). Missing: persisting rendered prompt & envelope to artifact store; no bundle_fingerprint returned. | Add bundle_fingerprint, write envelope/prompt to MinIO under request_id. |
| **Validator integration** | Gateway must run blocking validator and fall back if it fails | core_validator.validate_response() implements rules, but app.py imports it without ever calling it; fallback still handled by templater.validate_and_fix only. | Invoke validator after LLM / templater, record report, set meta.fallback_used. |
| **Structured logging & OTEL** | Every stage logs JSON with snapshot_etag, selector metrics, etc. | core_logging provides JSON formatter; gateway logs exist but builder/selector don't attach required fields; no OTEL spans around stages. | Wrap planner/selector/validator in @log_stage or spans, include required keys. |
| **Trace & artifact retention** | Persist Envelope → Prompt → Raw LLM → Validator → Final objects | Trace/MinIO module not present; no write_artifacts() call anywhere. | Add gateway.trace package and wire it from app.py. |
| **Constants single-source** | Use core_config.constants | Selector re-defines the same constants, risking drift. | Import only from core_config; delete local copies. |
| **Caching** | Evidence cache 15 min, keyed by snapshot_etag | EvidenceBuilder has correct hash & TTL (900 s) – ✅ | |
| **Tests / coverage** | Golden + validator + selector tests; coverage = 1.0 | New unit tests for selector & validator present, but no golden bundles nor integration path tests; pipeline coverage still <1.0. | Add frozen bundle fixtures + end-to-end test that exercises gateway → validator. |
| **Performance guards** | Selector truncation ≤2 ms, evidence size managed | Bundle size helper works; lack of similarity ranking may drop wrong items; no timing metrics. | Time selector & emit metric; consider asyncio timeout. |
| **Core-validator dependency** | Should be package-neutral (no reverse import) | core_validator imports gateway.models, creating a circular dependency violation. | Move shared Pydantic models to core_models & depend downward only. |

## Detailed Observations

### Selector Bugs

- Duplicate import orjson, datetime as dt & constant shadowing point to merge-conflict artefacts.
- `_score()` returns `(timestamp, 0.0)` — similarity never used.
- When truncating, loop breaks after `<= SELECTOR_TRUNCATION_THRESHOLD` but spec says trim until `MAX_PROMPT_BYTES`, then always keep `≥ MIN_EVIDENCE_ITEMS`. Edge-case logic is partly correct but stops too early.

### Validator Rules Coverage

**Present rules check:**
- Schema subset
- supporting_ids ⊆ allowed_ids
- Transition citation

**Missing checks:**
- short_answer ≤320 chars
- completeness_flags correctness
- retry count ≤2
- latency_ms populated

### Gateway Flow

- EvidenceBuilder fetches/caches bundle correctly, but allowed_ids recomputed both there and in selector — choose single authority.
- After evidence assembly, gateway goes straight to templater; LLM client & retries are still stubs (OK for M3), but validator/fallback not hooked.

### Logging / OTEL

- core_logging.log_stage decorator exists yet not applied in gateway modules; trace_id generation visible in logger tests but not in runtime path.
- No OpenTelemetry exporter initialisation; spec requires spans on M2+.

### Tests & CI

**Selector and validator tests good first pass. Still missing:**
- Golden evidence bundles (Why/Who/When)
- Integration test exercising gateway → memory-API stubs → validator
- Coverage measurement in CI workflow

## Immediate Action Items

### 1. Refactor selector.py

```python
from core_config.constants import MAX_PROMPT_BYTES, SELECTOR_TRUNCATION_THRESHOLD, MIN_EVIDENCE_ITEMS
...
def _score(item, anchor):  # include similarity
    ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    sim = _sim(item.get("summary") or item.get("description"), anchor.rationale)
    return (-ts.timestamp(), -sim)  # higher recency & similarity → smaller tuple
```

And keep pruning until `bundle_size_bytes(ev) <= MAX_PROMPT_BYTES`.

### 2. Wire validator & artifact trace

After templater/LLM call:

```python
ok, errs = validate_response(resp)
if not ok:
    resp.meta.fallback_used = True
    resp.answer.short_answer = deterministic_short_answer(...)
trace.write_artifacts(request_id, envelope, prompt, raw_json, errs, resp)
```

### 3. Finish structured logging

- Decorate planner, selector, validator functions with `@log_stage`.
- Include snapshot_etag, selector_truncation, bundle_size_bytes, etc.
- Split models to core_models so core_validator no longer depends on gateway internals.

### 4. Add golden bundles & CI gate

- Store snapshot fixtures under `services/gateway/tests/golden/`.
- In CI, compute coverage and assert `completeness_debt == 0`.