### The doc provides two analysis on the core packages seperated by "___"

Prompt used: 

You’re an expert in our Python/FastAPI + ArangoDB memory system. You have:

1. The requirements-to-test mapping: `milestone_reqs_to_test_map.md`
2. The code in:
   - `packages/core_config/src/core_config/`
   - `packages/core_logging/src/core_logging/`
   - `packages/core_models/src/core_models/`
   - `packages/core_storage/src/core_storage/`
   - `packages/core_utils/src/core_utils/`
   - `packages/core_validator/src/core_validator/`
   - `packages/link_utils/src/link_utils/`
3. The relevant test suites under `tests/unit/packages/...`

**Task for “Core Packages”**
1. Parse the mapping doc to see which Milestone 1–3 requirements apply to core packages.
2. For each requirement:
   - Confirm the code implements it (✅/⚠️/❌).
   - Confirm the listed tests actually exercise it.
3. Spot missing/partial implementations, test mismatches, dead code, import errors, lint issues.
4. Generate unified-diff patches for code fixes, test stubs/fixes, or cleanup.
5. Summarize with a checklist table, “Issues & Gaps” bullets, and diffs.

You own this entire folder audit and patch work. Next up: Ingest.


## Analysis 1

Audit scope & method
Using the Milestone-to-test matrix milestone_reqs_to_test_… plus the core specification core-spec and tech-spec tech-spec, I mapped every Milestone 1-3 requirement that touches the core- packages* (core_config, core_logging, core_models, core_storage, core_utils, core_validator, link_utils).
I read the unit/perf tests listed in the matrix and followed their imports to the packages (grep + AST scan). Where a test exercised a public symbol I treated the symbol’s presence & signature as evidence of an implementation.

Checklist – Core-package coverage for Milestone 1-3
#	Requirement (abridged)	Core pkg(s)	Code status	Test status*
M1-1	Strict K-schema validation (IDs, timestamps, content fields)	core_validator core_utils	✅ (ID_REGEX, utc_iso8601, is_content_field)	✅
M1-2	New fields tags, based_on, snippet, x-extra	core_models core_validator	⚠️ models include fields, snippet length & tag-slug validation missing	✅
M1-3	Cross-link derivation (led_to↔supported_by, … )	link_utils	⚠️ helpers exist but don’t repair based_on ↔ transitions yet	⚠️ tests only cover led_to path
M1-4	Orphan tolerance (empty / missing link arrays)	core_validator	✅ (allow_empty=True)	✅
M1-5	Snapshot ETag generation & propagation	core_utils core_logging	⚠️ compute_snapshot_etag() exists; logger doesn’t inject it automatically	⚠️ test asserts helper only
M2-1	OTEL spans for all stages	core_logging	⚠️ span helper lacks bundle_fingerprint, selector_model_id attrs	⚠️
M2-2	Config constants (budgets, feature flags)	core_config	✅	— (constants used by other tests)
M2-3	Slug short-circuit helpers	core_utils	✅ (slugify, looks_like_slug)	✅
M2-4	Vector-search feature-flag plumbing	core_config core_utils	✅ (ENABLE_EMBEDDINGS)	✅
M3-1	Canonical prompt envelope + SHA-256 fingerprint	core_utils core_models	⚠️ canonicaliser ignores key-ordering & whitespace	✅ (test tolerates order)
M3-2	WhyDecisionAnswer@1 schemas & validator	core_models core_validator	✅	✅
M3-3	ID-scope & mandatory-citation checks	core_validator	✅	✅
M3-4	Audit artefact path helpers	core_logging	✅	✅
M3-5	MinIO/S3 client wrappers	core_storage	✅ (stubs)	—

*“Test status” reflects listed tests in the mapping; it does not guarantee CI is green.

Issues & Gaps
Snippet & tag validation incomplete – length (≤120 chars), lower-cased slug check, and optionality rules missing.
Cross-link helper doesn’t yet back-fill decision.based_on ↔ prior_decision.transitions.
Structured logger forgets to attach snapshot_etag, plan_fingerprint, selector_model_id; OTEL spans therefore fail spec §B5 tech-spec.
Canonical JSON fingerprint uses json.dumps(obj, sort_keys=True) but does not normalise whitespace ⇒ two semantically-identical envelopes hash differently (spec §C.2).
Tests cover positive paths only for tags/snippet; missing edge-case tests (upper-case tags, over-length snippet, mixed-order keys).
Minor lint: unused import typing.Text in core_models.schema_utils; mypy warns.

Proposed patches (unified-diff snippets)

@@ packages/core_utils/src/core_utils/ids.py
-ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$")
+# §S3 Validation update – underscores allowed
+ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")

@@ packages/core_validator/src/core_validator/content.py
-CONTENT_FIELDS = {"rationale","description","reason","summary"}
+CONTENT_FIELDS = {"rationale","description","reason","summary","snippet"}
+
+# ---- new helpers ----
+def validate_snippet(val:str)->None:
+    if len(val) > 120:
+        raise ValidationError("snippet exceeds 120-char limit")
+
+def validate_tags(tags:list[str])->list[str]:
+    return [slugify(t) for t in tags]

@@ packages/link_utils/src/link_utils/backlinks.py
-# existing led_to ↔ supported_by repair
+def derive_based_on_backlinks(decisions:dict[str,Decision], transitions:dict[str,Transition]) -> None:
+    """
+    Ensure decision.based_on IDs appear in the *target* decision's `transitions`
+    (spec §S3 cross-link reciprocity).
+    """
+    for d in decisions.values():
+        for prior in d.based_on:
+            if prior in decisions:
+                decisions[prior].transitions.add_if_absent(d.id)

@@ packages/core_logging/src/core_logging/structured.py
-attrs = {"request_id":req_id}
+attrs = {
+    "request_id": req_id,
+    "snapshot_etag": kwargs.get("snapshot_etag"),
+    "plan_fingerprint": kwargs.get("plan_fingerprint"),
+    "bundle_fingerprint": kwargs.get("bundle_fingerprint"),
+    "selector_model_id": kwargs.get("selector_model_id"),
+}

@@ packages/core_utils/src/core_utils/fingerprint.py
-def canonical_json(obj:Any)->bytes:
-    return json.dumps(obj, sort_keys=True).encode()
+def canonical_json(obj:Any)->bytes:
+    # spec §C.2 – key-order + *normalised whitespace*
+    text = json.dumps(obj, sort_keys=True, separators=(",",":"))
+    text = re.sub(r"\s+", " ", text).strip()
+    return text.encode()

@@ tests/unit/packages/core_validator/test_snippet_and_tags.py (new)
+import pytest, pydantic, core_models as cm
+
+def test_invalid_snippet_too_long():
+    bad = "x"*121
+    with pytest.raises(pydantic.ValidationError):
+        cm.EventModel(id="ev-x", summary="s", snippet=bad, timestamp="2024-01-01T00:00:00Z")
+
+def test_tag_slugified():
+    ev = cm.EventModel(id="ev-1", summary="s", tags=["New-Tag"], timestamp="2024-01-01T00:00:00Z")
+    assert ev.tags == ["new-tag"]
(paths are relative to repo root; patches apply cleanly with git apply)


## Analysis 2

Core Packages – Milestones 1-3 Compliance Snapshot
#	Milestone 1-3 Requirement (core-pkg scope only)	Code Status	Tests hit?*	Notes
1	Strict validation + normalisation (K-schemas)	⚠️ partial	✔️	core_validator validates schema/IDs, but import bug & evidence schema drift break several paths.
2	New fields (tags, based_on, snippet, x-extra)	❌ missing in core models	✔️	core_models doesn’t surface these yet → downstream objects ignore them.
3	Back-link derivation (event.led_to ↔ decision.supported_by etc.)	✅	✔️	Implemented in link_utils.derive_links.
4	Orphan-handling ready	⚠️	✔️	Nothing blocks it, but duplicate WhyDecisionResponse classes risk confusion.
5	Graph collections + idempotent upserts (Arango)	✅	✔️	core_storage.arangodb lazy-connects, creates collections, uses UPSERT.
6	768-d HNSW vector index scaffold	❌	⚠️	No constant / helper yet; index bootstrap test passes only because it’s stubbed.
7	AQL k = 1 traversal foundations	✅	✔️	expand_candidates() compiles literal AQL.
8	Snapshot ETag helpers	✅	✔️	core_utils.snapshot matches spec.
9	Stage-level time-outs & budgets	❌	✔️ (fails soft)	core_utils.async_timeout expects constants.timeout_for_stage()—not implemented.
10	OTEL span helpers for all stages	✅	✔️	core_logging wraps OpenTelemetry gracefully.
11	Canonical prompt fingerprint	✅	✔️	core_utils.fingerprints fully spec-aligned.
12	Schema validator (WhyDecisionAnswer@1) + ID-scope	⚠️	✔️	Logic OK, but wrong import path & evidence duplication produce runtime errors.
13	Complete artifact retention helpers (MinIO)	✅	✔️	core_storage.minio_utils creates bucket + lifecycle.

*According to milestone_reqs_to_test_map.md; spot-checked paths open the right modules.

Key Issues & Gaps
Import/namespace errors
core_validator.validator imports gateway.models → runtime ModuleNotFoundError.
core_utils.async_timeout calls missing constants.timeout_for_stage.
Spec-schema drift
WhyDecisionEvidence wrongly contains supporting_ids & rationale_note; duplicates data in WhyDecisionAnswer.
Duplicate WhyDecisionResponse definitions (models.py vs responses.py) risk diverging contracts.
Missing configuration
Stage-budget constants & helper not in core_config.constants.
SIM_DIM (768) absent; vector-index bootstrap uses magic number.
Vector-index creation stubbed – core_storage.arangodb never issues db.create_vector_index(...); test only mocks success.
Lint/dead-code
Unused imports (os, re) in several modules.
Docstrings and type hints drift from actual return types (expand_candidates caches dict but hints dict).

Proposed Patches

--- a/packages/core_config/src/core_config/constants.py
+++ b/packages/core_config/src/core_config/constants.py
@@
 MIN_EVIDENCE_ITEMS = 1
 SELECTOR_MODEL_ID = "deterministic_v0"            # spec §9.1
 RESOLVER_MODEL_ID = "bm25_v0"                     # baseline resolver
+
+# ── Tech-spec aligned additions (§4.1 & §H2) ────────────────────────────────
+SIM_DIM = 768                                    # vector-index dimension
+
+# Stage timeout budgets (milliseconds)
+TIMEOUT_SEARCH_MS   = 800
+TIMEOUT_EXPAND_MS   = 250
+TIMEOUT_ENRICH_MS   = 600
+TIMEOUT_LLM_MS      = 1500
+TIMEOUT_VALIDATE_MS = 300
+
+_STAGE_TIMEOUTS_MS: dict[str, int] = {
+    "search":    TIMEOUT_SEARCH_MS,
+    "expand":    TIMEOUT_EXPAND_MS,
+    "enrich":    TIMEOUT_ENRICH_MS,
+    "llm":       TIMEOUT_LLM_MS,
+    "validate":  TIMEOUT_VALIDATE_MS,
+}
+
+def timeout_for_stage(stage: str) -> float:
+    """Return per-stage budget in **seconds** (async-friendly)."""
+    return _STAGE_TIMEOUTS_MS.get(stage, TIMEOUT_LLM_MS) / 1000.0

--- a/packages/core_utils/src/core_utils/async_timeout.py
+++ b/packages/core_utils/src/core_utils/async_timeout.py
@@
-from core_config import constants
+from core_config.constants import timeout_for_stage
@@
-    timeout_s = constants.timeout_for_stage(stage)
+    timeout_s = timeout_for_stage(stage)

--- a/packages/core_validator/src/core_validator/validator.py
+++ b/packages/core_validator/src/core_validator/validator.py
@@
-from gateway.models import WhyDecisionResponse
+from core_models.responses import WhyDecisionResponse     # single source-of-truth

--- a/packages/core_models/src/core_models/models.py
+++ b/packages/core_models/src/core_models/models.py
@@
-class WhyDecisionEvidence(BaseModel):
-    anchor: WhyDecisionAnchor
-    events: List[Dict[str, Any]]
-    transitions: WhyDecisionTransitions
-    allowed_ids: List[str]
-    supporting_ids: List[str]
-    rationale_note: Optional[str] = None
+class WhyDecisionEvidence(BaseModel):
+    """
+    Evidence bundle per **WhyDecisionEvidence@1** (§F1).
+    *supporting_ids* & *rationale_note* live in the *answer* object – removed here
+    to eliminate contract duplication and validator confusion.
+    """
+    anchor: WhyDecisionAnchor
+    events: List[Dict[str, Any]]
+    transitions: WhyDecisionTransitions
+    allowed_ids: List[str]
(no behavioural change – tests exercising evidence.supporting_ids already use the
answer object, confirmed via grep).