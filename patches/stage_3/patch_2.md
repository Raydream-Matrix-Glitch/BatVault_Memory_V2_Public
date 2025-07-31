# Patch 1 Review - Blocking Issues & Fixes

## Overall Verdict
Patch 1 closes many Milestone-3 gaps, but several blocking defects and spec-conformance issues remain. **The first two bullets are show-stoppers** before merge.

---

## 🚨 Blocking Issues

### 1. Evidence Cache Key Failure
**Problem**: `ev_headers` is referenced before it exists in `EvidenceBuilder.build()`
- **Location**: `services/gateway/src/gateway/evidence.py`
- **Impact**: Raises `NameError` and never hits Redis cache
- **Root Cause**: Call to `_make_cache_key(..., snapshot_etag=ev_headers.get(...))` before `ev_headers` is defined
- **Additional Issue**: Even after fixing, cache reads will always miss because `snapshot_etag` is unknown before upstream call

### 2. Cache Invalidation Design Flaw
**Problem**: Spec (§9.3) requires cache invalidation on `snapshot_etag`, but current design causes every read to miss
- **Location**: Core-spec §9.3 requirement
- **Impact**: Cache is effectively non-functional
- **Solution Needed**: Two-key strategy (pointer key → composite key) or dual alias storage

---

## 🔧 Critical Fixes Required

### 3. Selector Observability - Missing Fields
**Problem**: Patch 1 dropped mandatory fields from `selector_meta`
- **Location**: `services/gateway/src/gateway/selector.py`
- **Missing Fields**: 
  - `final_evidence_count`
  - `dropped_evidence_ids`
- **Spec Reference**: Tech-spec §9.1
- **Note**: Fields were present in Patch 0 but missing in both truncation branches now

### 4. Prompt Envelope Schema Mismatch
**Problem**: Envelope structure doesn't match spec
- **Location**: `gateway/prompt_envelope.py`, `gateway/app.py`
- **Issues**:
  - `prompt_id` value stored in field called `prompt_version`
  - Missing explicit `prompt_id` in envelope
  - Downstream meta copies `prompt_version` back to `prompt_id` (brittle)
- **Spec Reference**: Tech-spec §8.2 shows both `prompt_id` and `policy_id` as top-level envelope fields

### 5. Policy Registry Duplication
**Problem**: JSON file + in-memory `_POLICY_REGISTRY` can drift
- **Location**: `gateway/prompt_envelope.py`, `services/gateway/config/policy_registry.json`
- **Issue**: Builder ignores external registry, uses hard-coded dict
- **Solution**: Read JSON once at startup, drop hard-coded dict

### 6. Snapshot ETag Not Attached
**Problem**: ETag collected but never attached to evidence instance
- **Location**: `gateway/evidence.py`, `gateway/app.py`
- **Flow**: `_collect_from_upstream()` returns header, but `WhyDecisionEvidence` instance never gets it
- **Result**: `meta["snapshot_etag"] = getattr(ev, "snapshot_etag", "unknown")` always returns "unknown"

---

## ⚠️ Additional Issues

### 7. Validator Import Path Risk
**Problem**: Circular import risk with `WhyDecisionAnswer`
- **Location**: `packages/core_validator/src/core_validator/validator.py`
- **Issue**: Imports from `gateway.models` but `__all__` may not expose it
- **Risk**: Gunicorn deployment failures

### 8. Meta Field Gaps
**Problem**: Response meta missing spec-required fields
- **Spec Reference**: Tech-spec §8.3, Table 7
- **Missing**: 
  - `retries` ❌
  - `gateway_version` ❌
  - `sdk_version` ❌
- **Present**: `selector_model_id` ✅

### 9. Test Dependencies
**Problem**: Tests assume fixture tarball not in repo
- **Impact**: CI will fail
- **Missing**: `tests/fixtures/batvault_live_snapshot.tar.gz`

---

## 🛠️ Suggested Fixes

### Fix 1: Re-work Cache Lookup Flow
```python
# Two-key strategy for cache invalidation
alias_key = f"evidence:{anchor_id}:latest"
composite_key = None

if redis.exists(alias_key):
    composite_key = redis.get(alias_key).decode()
    cached = redis.get(composite_key)
    if cached:
        return parse_cached_evidence(cached)

# On cache miss:
ev, hdrs = _collect_from_upstream(...)
composite_key = _make_cache_key(..., snapshot_etag=hdrs["snapshot_etag"], ...)
redis.mset({
    composite_key: ev_json, 
    alias_key: composite_key
})
```

### Fix 2: Restore Selector Meta Fields
```python
# Keep Patch-0 structure and append selector_model_id
selector_meta = {
    "final_evidence_count": len(final_evidence),
    "dropped_evidence_ids": [item.id for item in dropped_items],
    "selector_model_id": "v1.0",
    # ... other existing fields
}
```

### Fix 3: Normalize Envelope Schema
```python
# Rename prompt_version → prompt_id in envelope
envelope = {
    "prompt_id": "why_v1",        # Not prompt_version
    "policy_id": "standard_v1",   # Explicit field
    "intent": "why_decision",
    # ... rest of envelope
}
```

### Fix 4: Single-Source Policy Registry
```python
# Load once at startup
def load_policy_registry():
    with open("config/policy_registry.json") as f:
        return json.load(f)

# Drop _POLICY_REGISTRY hard-coded dict
```

### Fix 5: Attach Snapshot ETag
```python
# Add field to WhyDecisionEvidence
class WhyDecisionEvidence(BaseModel, extra="allow"):
    # ... existing fields
    snapshot_etag: Optional[str] = None

# In evidence builder:
evidence.snapshot_etag = snapshot_etag
```

### Fix 6: Complete Meta Fields
```python
meta = {
    # ... existing fields
    "retries": retry_count,
    "gateway_version": __version__,
    "sdk_version": "1.0.0",
}
```

### Fix 7: Guard Imports & Commit Fixtures
- Ensure `WhyDecisionAnswer` in `gateway.models.__init__.__all__`
- Commit `tests/fixtures/batvault_live_snapshot.tar.gz`

---

## 📋 Acceptance Checklist

- [ ] Evidence cache works with two-key strategy
- [ ] All selector meta fields present and logged
- [ ] Prompt envelope matches spec schema exactly
- [ ] Single policy registry source (JSON file)
- [ ] Snapshot ETag attached to evidence instances
- [ ] All spec-required meta fields included
- [ ] Import paths secured against circular dependencies
- [ ] Test fixtures committed and CI passes
- [ ] Retry jitter documented and attempt count logged
- [ ] Bundle size computation optimized (single calculation)
