Quick verdict
Solid first pass — the new modules slot cleanly into the Gateway and most of the milestone-3 surface area is covered.
Below is a punch-list of blocking gaps we must close before the patch can land, followed by some smaller polish items.

1 Evidence builder & caching
❌	Issue	Why it matters
1.1	Cache key is only evidence:{anchor_id}【5:patch_0.md†L41-L44】	Spec requires the composite key (decision_id, intent, graph_scope, snapshot_etag, truncation_applied) so that bundles invalidate when ingest publishes a new snapshot【5:tech-spec.md†L48-L51】.
1.2	We never read/forward the snapshot_etag header returned by Memory-API (needed for key + logs/audit).	Required by audit trail (§B5) and acceptance-criteria 100 % traceability.
1.3	No HTTP retry/back-off when calling Memory-API. Spec allows one retry with jitter ≤ 300 ms【8:tech-spec.md†L44-L46】.	Prevents transient network errors from bubbling up to users.

Fix
Compose the Redis key from the 5-tuple above and include snapshot_etag from anchor_data.headers.

Add a single httpx.RetryTransport (or manual retry) with capped jitter.

2 Selector
❌	Issue	Why it matters
2.1	Scorer stub only returns recency; similarity is always 0.0【2:patch_0.md†L45-L48】.	Milestone-3 deliverable: deterministic recency + similarity baseline【5:project_development_milestones.md†L52-L55】.
2.2	Constant MIN_EVIDENCE_ITEMS = 1 is not enforced (anchor + ≥1 neighbour)【15:tech-spec.md†L10-L13】.	Prevents accidental empty bundles after truncation.
2.3	We log selector_truncation, counts, etc., but selector_model_id is missing (should be "deterministic_v0")【9:tech-spec.md†L11-L12】.	Required for observability dashboards.

3 Prompt envelope & meta
❌	Issue	Why it matters
3.1	Envelope omits policy block & explanations fields mandated by the canonical schema【9:tech-spec.md†L61-L70】.	
3.2	meta lacks policy_id and prompt_id, yet every response must carry them together with prompt_fingerprint【15:tech-spec.md†L20-L22】【9:core-spec.md†L47-L48】.	
3.3	The envelope isn’t persisted to MinIO / artifact sink (envelope → rendered prompt → raw LLM → validator → final) — part of milestone-3 audit trail【5:project_development_milestones.md†L57-L60】.	

Fix
Extend build_envelope() to accept / inject policy and explanations.

Introduce a simple policy_registry.json stub with one entry (why_v1) and wire policy_id/prompt_id into meta.

Reuse existing MinIO client to put_object the envelope using {request_id}/envelope.json.

4 Validator
❌	Issue	Why it matters
4.1	Only ID-level checks are implemented. Missing schema validation against WhyDecisionAnswer@1 (spec lists it as the first rule)【12:tech-spec.md†L54-L57】.	
4.2	Unit tests cover subset rule only; need cases for “anchor missing”, “transition ids missing”, and schema errors.	

Fix
Import the generated Pydantic model for WhyDecisionAnswer@1 and call model_validate (catch ValidationError).

Add three more pytest cases (invalid schema, missing anchor, missing transition ids).

5 Logging & metrics
Evidence stage log needs total_neighbors_found on the non-truncated path (already present), plus selector_model_id (see §2.3).

bundle_fingerprint is not computed/stored yet (SHA-256 of minified bundle) — required for LLM-JSON cache key and replay tooling【9:core-spec.md†L1-L4】.

6 Smaller polish / nice-to-haves
Propagate MAX_PROMPT_BYTES from config instead of hard-coding in selector.py.

Wrap the httpx.Client in a context-manager (with or .close() in __del__) to avoid socket leaks.

Consider moving truncate_evidence() into its own module under evidence/selector_baseline.py to leave room for the future ML model.

✅ What already looks good
Cache TTL matches spec (900 s)【1:patch_0.md†L23-L24】.

Deterministic fallback path wired: validation failure → templater + fallback_used=true【5:patch_0.md†L33-L40】.

Selector metadata (dropped_evidence_ids, bundle_size_bytes) logged as required【4:patch_0.md†L34-L41】.

Initial unit test scaffold in services/gateway/tests/test_validator.py.