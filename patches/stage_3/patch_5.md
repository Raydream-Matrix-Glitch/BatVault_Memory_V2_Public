⚠️ Remaining gaps before we can call M3 “done”
Area	Gap	Spec reference
Audit trail storage	Envelope is created and fingerprints are logged, but the code still doesn’t persist any of the required artefacts to MinIO (envelope / rendered prompt / raw LLM JSON / validator report / final response).	“Every response… artefacts are persisted” tech-spec
Test coverage & quality gates	We now have two validator unit tests; spec calls for full validator edge-cases plus golden suites for why/who/when and router contract tests, with coverage = 1.0 & completeness_debt = 0 — still missing.	Core-spec §14 missing-tests list and quality-gate item 5 core-spec
TTL hygiene	Alias key currently expires at the same 15-min TTL as the bundle. That’s acceptable, but the spec’s intent is only “invalidate on snapshot_etag change” – you might consider no TTL on the alias or a short Lua script that rewrites it on ETag change to avoid a double miss window.	Cache policy table core-spec
Policy registry packaging	services/gateway/config/policy_registry.json is referenced but not added to MANIFEST.in / package-data. Make sure it ships in wheels & Docker images.	
Selector logs in app response	selector_meta carries total_neighbors_found, selector_truncation, etc., but app.py only forwards selector_model_id. Decide whether those belong in meta (client visible) or only in logs and wire accordingly.	
Load-shedding & artefact retention completeness flags	Still TODO (part of Milestone 4, but the validator already expects them).	

🧪 What tests are still needed
Validator edge cases (listed in core-spec §14.1):

decision_no_transitions.json, event_orphan.json, decision_missing_transitions_field.json.

Golden answer tests for why/who/when using frozen evidence bundles (tech-spec M6).

Selector truncation: feed an oversize bundle and assert that

selector_truncation=true,

dropped IDs match expectation,

allowed_ids reflects the kept subset.

Two-key cache behaviour (can be unit-level with a Redis fixture):

write bundle, expire composite key only → alias hit must trigger refresh and rewrite.

Artifact-retention smoke: after a request, verify MinIO contains 5 artefacts whose filenames include prompt_fingerprint.

🔍 Suggested micro-fixes
Alias TTL – if you stick with a TTL, set alias_key first to avoid a race where the bundle is written but alias isn’t (rare but easy).

Prompt_id / policy_id exposure – app.py now surfaces them, good. Make sure the validator checks they are non-empty (it currently doesn’t).

WhyDecisionEvidence.allowed_ids maintenance – non-truncation path is fine, but add an assertion in unit tests that allowed_ids == all present IDs after every builder call.

Packaging – add:

toml
Copy
Edit
[tool.setuptools.package-data]
"gateway.config" = ["policy_registry.json"]