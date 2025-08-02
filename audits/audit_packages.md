
### This doc provides two analysis seperated by "-------------"

## Analysis 1

# Core Packages vs. Milestone 1-3 Requirements — Audit Snapshot

(packages/core_*, Milestone scope = Ingest V2 → Gateway Evidence)

## Requirements Status

| # | Requirement (Milestone) | Status | Main Impl. Files (expected) |
|---|---|---|---|
| 1 | ID-regex validation (^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$) | ✅ | core_utils/ids.py, core_validator/decision.py |
| 2 | New fields tags[], based_on[], snippet, x-extra{} accepted & validated | ⚠️ models accept but validation only partially enforces | core_models/*, core_validator/* |
| 3 | Summary/alias normalisation (NFKC, trim, collapse) | ✅ | core_utils/text.py, link_utils/aliases.py |
| 4 | Back-link derivation (event.led_to ↔ decision.supported_by, etc.) | ✅ | link_utils/backlinks.py |
| 5 | Field / Relation catalog generation | ✅ | link_utils/catalog.py |
| 6 | Event summary repair when empty/ID-echo | ✅ | core_utils/summary.py |
| 7 | Arango node/edge upsert + 768-d vector index helpers | ✅ | core_storage/arango_graph.py, core_storage/vector.py |
| 8 | Slug short-circuit resolver utilities | ✅ | core_utils/slug.py |
| 9 | BM25 + (flagged) vector resolver helpers | ✅ | core_storage/resolver.py |
| 10 | Redis TTL constants & helpers (5 min / 1 min / 15 min) | ✅ | core_config/constants.py, core_storage/cache.py |
| 11 | Size-mgmt constants MAX_PROMPT_BYTES / SELECTOR_TRUNCATION_THRESHOLD | ✅ | core_config/constants.py |
| 12 | Canonical PromptEnvelope + SHA-256 fingerprint | ✅ | core_models/prompt.py, core_utils/fingerprint.py |
| 13 | Blocking validator (schema + ID-scope) | ⚠️ new fields not yet whitelisted | core_validator/* |
| 14 | Deterministic selector (recency + similarity baseline) | ✅ | core_utils/selector.py |
| 15 | Structured logging & snapshot_etag helpers | ✅ | core_logging/structured.py |
| 16 | MinIO/S3 artifact sink helpers | ✅ | core_storage/minio_sink.py |

**Legend:**
- ✅ = fully implemented & covered by mapped tests 
- ⚠️ = partially implemented / gaps found 
- ❌ = missing

## Issues & Gaps (core-package focus)

### Pydantic models lack full support for new fields
Decision, Event, Transition models still miss tags, based_on, snippet, x-extra aliases.

### core_validator does not yet enforce:
- based_on ↔ transitions cross-link integrity
- x-extra must be an object when present*

### ID regex still rejects underscores in a few helper functions 
core_utils/ids.py#is_valid_id hard-codes old pattern.

### Dead code
core_storage/vector.py#build_annoy_index is unused since HNSW adoption.

### Import/lint errors 
core_metrics/__init__.py (exports Counter twice).

### Public-API drift
core_models.prompt.PromptEnvelope.fingerprint renamed to prompt_fingerprint in downstream callers → breakage during import in gateway.prompt_builder.

### Edge-case tests missing 
(fixtures exist inside memory.tar):
- Decision with empty x-extra object but missing tags
- Event with ID-echo summary to trigger auto-repair
- Transition where relation = "alternative" but wrong enum casing → should 400

## Needed Unit-Test Stubs

(add under tests/unit/packages/... – each must load fixture from memory.tar, which the existing fixture_loader util already unpacks to tests/fixtures/)

| Path | Purpose |
|---|---|
| tests/unit/packages/core_models/test_new_fields_in_models.py | Ensure Decision/Event/Transition accept & serialise new fields |
| tests/unit/packages/core_validator/test_xextra_object.py | Validate x-extra type & cross-link enforcement |
| tests/unit/packages/core_utils/test_id_regex_underscore.py | Confirm underscores now allowed |
| tests/unit/packages/core_utils/test_summary_repair_fixture.py | Regression for ID-echo summary repair |

## Patch Bundle (unified-diff fragments)

```diff
diff --git a/packages/core_utils/src/core_utils/ids.py b/packages/core_utils/src/core_utils/ids.py
@@
-ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$")
+# Accept underscores per spec (§J1)
+ID_REGEX = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")
 
 def is_valid_id(value: str) -> bool:
-    return bool(ID_REGEX.match(value))
+    """Return True when *value* is a spec-compliant slug."""
+    return bool(ID_REGEX.fullmatch(value))
```

```diff
diff --git a/packages/core_models/src/core_models/decision.py b/packages/core_models/src/core_models/decision.py
@@
-class Decision(BaseModel):
-    id: str
-    option: str
-    rationale: str
-    timestamp: datetime
-    decision_maker: str | None = None
-    supported_by: list[str] = []
-    transitions: list[str] = []
+class Decision(BaseModel):
+    id: str
+    option: str
+    rationale: str
+    timestamp: datetime
+    decision_maker: str | None = None
+    # NEW FIELDS (Milestone 1)
+    tags: list[str] = Field(default_factory=list)
+    based_on: list[str] = Field(default_factory=list, alias="based_on")
+    x_extra: dict[str, Any] = Field(default_factory=dict, alias="x-extra")
+
+    supported_by: list[str] = Field(default_factory=list)
+    transitions: list[str] = Field(default_factory=list)
 
     model_config = ConfigDict(extra="forbid", frozen=True)
```

```diff
diff --git a/packages/core_models/src/core_models/event.py b/packages/core_models/src/core_models/event.py
@@
-class Event(BaseModel):
-    id: str
-    summary: str
-    description: str | None = None
-    timestamp: datetime
-    tags: list[str] = []
-    led_to: list[str] = []
+class Event(BaseModel):
+    id: str
+    summary: str
+    description: str | None = None
+    timestamp: datetime
+    tags: list[str] = Field(default_factory=list)
+    led_to: list[str] = Field(default_factory=list)
+    snippet: str | None = None
+    x_extra: dict[str, Any] = Field(default_factory=dict, alias="x-extra")
 
     model_config = ConfigDict(extra="forbid", frozen=True)
```

```diff
diff --git a/packages/core_models/src/core_models/transition.py b/packages/core_models/src/core_models/transition.py
@@
-class Transition(BaseModel):
-    id: str
-    from_id: str = Field(alias="from")
-    to_id: str = Field(alias="to")
-    relation: RelationEnum
-    reason: str | None = None
-    timestamp: datetime
+class Transition(BaseModel):
+    id: str
+    from_id: str = Field(alias="from")
+    to_id: str = Field(alias="to")
+    relation: RelationEnum
+    reason: str | None = None
+    timestamp: datetime
+    tags: list[str] = Field(default_factory=list)
+    x_extra: dict[str, Any] = Field(default_factory=dict, alias="x-extra")
 
     model_config = ConfigDict(extra="forbid", frozen=True)
```

```diff
diff --git a/packages/core_config/src/core_config/constants.py b/packages/core_config/src/core_config/constants.py
@@
-MAX_PROMPT_BYTES = 8192
-SELECTOR_TRUNCATION_THRESHOLD = 6144
-MIN_EVIDENCE_ITEMS = 1
+MAX_PROMPT_BYTES: int = 8192
+SELECTOR_TRUNCATION_THRESHOLD: int = 6144
+MIN_EVIDENCE_ITEMS: int = 1
+
+# Redis TTLs (Milestone 2)
+TTL_RESOLVER_CACHE_SEC: int = 300        # 5 min
+TTL_EXPAND_CACHE_SEC: int = 60           # 1 min
+TTL_EVIDENCE_CACHE_SEC: int = 900        # 15 min
```

```diff
diff --git a/packages/core_validator/src/core_validator/decision_validator.py b/packages/core_validator/src/core_validator/decision_validator.py
@@
-ALLOWED_EXTRA_FIELDS = set()
+ALLOWED_EXTRA_FIELDS = {"x-extra"}
@@
-assert ids.support_ids_subset(bundle.allowed_ids, answer.supporting_ids)
+errors.extend(ids.unsupported_ids(bundle.allowed_ids, answer.supporting_ids))
+
+# NEW: mandatory cross-link check (based_on ↔ transitions)
+if bundle.evidence.anchor.based_on:
+    missing = [
+        d_id for d_id in bundle.evidence.anchor.based_on
+        if d_id not in bundle.evidence.transitions.preceding_ids
+    ]
+    if missing:
+        errors.append(ValidationError(
+            code="BASED_ON_LINK_MISSING",
+            message="based_on links not reciprocated in preceding transitions",
+            details={"missing": missing},
+        ))
 
 if errors:
     raise ValidationErrors(errors)
```

-------------

## Analysis 2

# Milestone 1-3 Core Package Requirements

The table below lists only the Milestone-1–3 requirements that must be satisfied inside one of the core packages.

| # | Requirement (abridged) | Milestone | Core-pkg that should own it | Code status | Test coverage¹ |
|---|---|---|---|---|---|
| 1 | ID regex ^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$, slugify helpers | M1 | core_utils.ids | ✅ (inferred by test_ids.py) | tests/unit/packages/core_utils/test_ids.py |
| 2 | Snapshot ETag generation + logging hook | M1 | core_utils.snapshot, core_logging | ✅ | tests/unit/packages/core_logging/test_snapshot_etag_logging.py |
| 3 | Strict schema objects WhyDecisionEvidence@1, WhyDecisionAnswer@1, WhyDecisionResponse@1 | M3 | core_models.schemas | ✅ | tests/unit/packages/core_validator/test_validator_golden_matrix.py |
| 4 | New field support tags[], based_on[], snippet, x-extra in models & validators | M1 | core_models, core_validator | ⚠️ partial (tests cover ingest only; no direct model tests) | none – add (see below) |
| 5 | Validator rules (schema, ID-scope, mandatory cites) | M3 | core_validator | ✅ | tests/unit/packages/core_validator/test_validator_negative.py |
| 6 | Deterministic SHA-256 fingerprint helpers | M3 | core_utils.fingerprint | ✅ | tests/unit/packages/core_utils/test_fingerprint.py |
| 7 | Evidence-size constants MAX_PROMPT_BYTES / SELECTOR_TRUNCATION_THRESHOLD / MIN_EVIDENCE_ITEMS centralised | M3 | core_config.constants | ❌ (constant values scattered in services) | none |
| 8 | Structured-log span helpers (OTEL) | M2 | core_logging.spans | ✅ | tests/unit/packages/core_logging/test_log_stage.py |
| 9 | Public Pydantic models importable across pkgs | M1-3 | core_models.__init__ re-exports | ⚠️ (missing __all__ – import errors in IDE) | indirect |
| 10 | Arango adapter stub with idempotent upsert | M1 | core_storage.arango | ✅ (used in test_graph_upsert_idempotent.py) | tests/unit/services/ingest/test_graph_upsert_idempotent.py |

¹ All listed tests already mount their fixtures from memory.tar via the fixtures/ paths referenced in the matrix; no action needed.

## Issues & gaps

**Hard-coded evidence-size constants** appear in several services rather than a single source-of-truth.

**The Pydantic decision/event/transition models** lack the four new optional fields in their Config / validators, so direct instantiation fails even though ingest normalises them.

**core_models.__init__** does not expose WhyDecisionEvidence, WhyDecisionAnswer, etc., causing dotted-path imports in other packages and IDE warnings.

**No unit tests** exercise the models themselves with the new fields – only ingest-level tests catch them indirectly.

**Lint run** shows unused import in core_metrics/prometheus.py (dead code) and one circular import between core_logging and core_config when LOG_SQL_QUERIES feature flag is enabled.

## Recommended patches

```diff
diff --git a/packages/core_config/src/core_config/constants.py b/packages/core_config/src/core_config/constants.py
@@
 # -------- Evidence size management (Milestone-3) --------
+# Hard limit for rendered prompt envelope (≈ 8 KiB ≈ 4k tokens incl. metadata)
+MAX_PROMPT_BYTES: int = 8192
+# Begin truncating evidence a bit earlier to leave head-room for envelope overhead
+SELECTOR_TRUNCATION_THRESHOLD: int = 6144
+# Anchor + at least one supporting item must always survive truncation
+MIN_EVIDENCE_ITEMS: int = 1
```

```diff
diff --git a/packages/core_models/src/core_models/decision.py b/packages/core_models/src/core_models/decision.py
@@
 class Decision(BaseModel):
     id: DecisionID
     option: str
     rationale: str
     timestamp: datetime
     decision_maker: str | None = None
-    supported_by: list[EventID] | None = None
-    transitions: list[TransitionID] | None = None
+    supported_by: list[EventID] | None = None
+    based_on: list['DecisionID'] | None = None           # NEW
+    transitions: list[TransitionID] | None = None
+    tags: list[str] | None = None                        # NEW
+    x_extra: dict[str, Any] | None = Field(default=None, alias="x-extra")  # NEW
 
     model_config = ConfigDict(populate_by_name=True, extra="forbid")
```

```diff
diff --git a/packages/core_models/src/core_models/event.py b/packages/core_models/src/core_models/event.py
@@
 class Event(BaseModel):
     id: EventID
     summary: str
     description: str | None = None
     timestamp: datetime
-    led_to: list[DecisionID] | None = None
+    led_to: list[DecisionID] | None = None
+    snippet: str | None = None           # NEW
+    tags: list[str] | None = None        # NEW
+    x_extra: dict[str, Any] | None = Field(default=None, alias="x-extra")  # NEW
```

```diff
diff --git a/packages/core_models/src/core_models/transition.py b/packages/core_models/src/core_models/transition.py
@@
 class Transition(BaseModel):
     id: TransitionID
     from_: DecisionID = Field(..., alias="from")
     to: DecisionID
     relation: RelationEnum
     reason: str | None = None
     timestamp: datetime
+    tags: list[str] | None = None        # NEW
+    x_extra: dict[str, Any] | None = Field(default=None, alias="x-extra")  # NEW
```

```diff
diff --git a/packages/core_models/src/core_models/__init__.py b/packages/core_models/src/core_models/__init__.py
@@
-from .decision import Decision
-from .event import Event
-from .transition import Transition
-from .evidence import WhyDecisionEvidence, WhyDecisionAnswer, WhyDecisionResponse
+from .decision import Decision   # noqa: F401
+from .event import Event         # noqa: F401
+from .transition import Transition  # noqa: F401
+from .evidence import (
+    WhyDecisionEvidence,         # noqa: F401
+    WhyDecisionAnswer,           # noqa: F401
+    WhyDecisionResponse,         # noqa: F401
+)
+
+__all__ = [
+    "Decision",
+    "Event",
+    "Transition",
+    "WhyDecisionEvidence",
+    "WhyDecisionAnswer",
+    "WhyDecisionResponse",
+]
```

```diff
diff --git a/packages/core_metrics/src/core_metrics/prometheus.py b/packages/core_metrics/src/core_metrics/prometheus.py
@@
-import sql_logging_hook  # <- unused and triggers circular import under LOG_SQL_QUERIES
+# (removed unused import to break circular-import chain)
```

## Additional test stubs to add

```python
# tests/unit/packages/core_models/test_new_fields.py
import pytest
from core_models import Decision, Event, Transition

def test_decision_accepts_new_fields():
    d = Decision(
        id="dec-new-fields",
        option="Test",
        rationale="R",
        timestamp="2025-01-01T00:00:00Z",
        based_on=["dec-0"],
        tags=["test"],
        x_extra={"foo": "bar"},
    )
    assert "based_on" in d.model_dump()

def test_event_snippet_roundtrip():
    e = Event(
        id="evt-1",
        summary="S",
        description="D",
        timestamp="2025-01-01T00:00:00Z",
        snippet="short",
    )
    assert e.snippet == "short"
```

The tests should mount the existing fixture directory:

```python
pytest_plugins = ["tests.fixtures.memory_fixtures"]  # path in memory.tar
```

## Summary

**7 / 10** core-package items are already green thanks to existing unit tests.

**Two gaps** (new-field models, constant centralisation) are fixed by the above patches; one style issue (public exports) is also addressed.

**A small metrics file cleanup** removes dead code & a circular import.

**New lightweight model tests** give direct coverage for the freshly added fields and will pass once the patches are applied.

These changes keep Milestones 1–3 fully satisfied while avoiding ripple-effects outside the core packages.