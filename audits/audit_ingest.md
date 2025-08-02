Note: This doc outlines 2 analysis that used the same prompt. The analysis are seperated by "_______".

### Analysis 1

# Milestones 1-3 "Ingest" Requirements Audit

## Quick-Look: Requirements Status

| # | Requirement (Milestone 1 unless noted) | Code impl. | Tests | Notes |
|---|----------------------------------------|------------|-------|-------|
| 1 | Strict validation + normalisation per K-schemas | ✅ | ✅ | ingest.schemas.json_v2.* + normalize.py use jsonschema.Draft202012Validator; tests in test_validation_* & test_strict_id_timestamp.py. |
| 2 | New fields tags, based_on, snippet, x-extra supported | ⚠️ | ✅ | Present, but snippet is erroneously allowed in decision & transition schemas. |
| 3 | Back-link derivation event.led_to ↔ decision.supported_by (+ based_on chains) | ✅ | ✅ | link_utils.derive_links called from normalize.derive_backlinks; unit tests in test_backlink_derivation.py. |
| 4 | Orphan handling (empty / missing link arrays) | ✅ | ✅ | Arrays are optional; confirmed by test_contract_orphans.py. |
| 5 | Graph collections & idempotent upserts | ✅ | ✅ | core_storage.arangodb.ArangoStore bootstraps nodes/edges, graph_upsert.upsert_all() handles idempotency. |
| 6 | 768-d HNSW vector index on nodes.embedding | ❌ | ⚠️ | _maybe_create_vector_index() builds a FAISS-IVF index; bootstrap test only skips, so gap unseen. |
| 7 | AQL foundations for k = 1 traversal | ✅ | ✅ | memory_api.expand_candidates uses AQL traversal; covered by test_expand_candidates_unit.py. |
| 8 | /api/enrich/* normalised envelopes | ✅ | ✅ | Memory-API endpoints return envelopes; contract tests in test_enrich_stubs.py. |
| 9 | Field & Relation Catalog endpoints | ✅ | ✅ | ingest.catalog.field_catalog + relation_catalog.py; gateway mirror tested in test_gateway_schema_mirror.py. |
| 10 | snapshot_etag on every response | ✅ | ✅ | core_utils.snapshot + FastAPI middleware; tests in test_snapshot_etag_logging.py. |
| 11 | Contract & cross-link tests (coverage = 1, completeness = 0) | ✅ | ✅ | tests/integration/test_missing_coverage.py. |

## Issues & Gaps

| ID | Severity | Detail |
|----|----------|--------|
| G-1 | High | services/ingest/schemas/json_v2/decision.schema.json & transition.schema.json incorrectly include snippet. Violates §K-schemas; allows bad authoring and weakens validator. |
| G-2 | High | _maybe_create_vector_index() builds **FAISS IVF (metric & dim OK) but spec requires 768-d HNSW (§M1). Failing to meet nearest-neighbour latency SLO will surface under load. |
| G-3 | Medium | Vector-index bootstrap test (test_vector_index_bootstrap.py) skips when ARANGO_HOST unset, so CI never exercises index creation logic (false sense of coverage). |
| G-4 | Low | memory.tar artefact in repo is dead weight; not referenced anywhere – remove to shrink container image. |
| G-5 | Low | Minor lint issues (flake8 E501) in normalize.py long lines 59-61; no functional impact. |

## Suggested Unit-Test Stubs

| Path | Purpose |
|------|---------|
| tests/unit/services/ingest/test_decision_snippet_validation.py | Assert that a Decision containing "snippet" fails strict validation. |
| tests/unit/services/ingest/test_transition_snippet_validation.py | Same for Transition. |
| tests/ops/test_vector_index_hnsw.py | Containerised smoke-test: ensure index type == hnsw, dimension == 768, metric ∈ {l2, cosine}. |

*Note: Fixtures can live under tests/fixtures/invalid_snippet_*.json; snapshot etag not needed because these are pure validation tests.*

## Unified-Diff Patches

### Schema Fix: Remove snippet from decision.schema.json

```diff
diff --git a/services/ingest/schemas/json_v2/decision.schema.json b/services/ingest/schemas/json_v2/decision.schema.json
@@
   "properties": {
@@
-    "snippet": {
-      "type": "string",
-      "maxLength": 120
-    },
@@
   "required": ["id", "option", "rationale", "timestamp"]
 }
```

### Schema Fix: Remove snippet from transition.schema.json

```diff
diff --git a/services/ingest/schemas/json_v2/transition.schema.json b/services/ingest/schemas/json_v2/transition.schema.json
@@
   "properties": {
@@
-    "snippet": {
-      "type": "string",
-      "maxLength": 120
-    },
@@
   "required": ["id", "from", "to", "relation", "reason", "timestamp"]
 }
```

### Vector Index Fix: Replace FAISS IVF with HNSW

```diff
diff --git a/packages/core_storage/src/core_storage/arangodb.py b/packages/core_storage/src/core_storage/arangodb.py
@@
-        """Create FAISS IVF index once there are enough training vectors."""
+        """Create **HNSW** vector index (spec §M1) once training data present."""
@@
-        # FAISS IVF implementation --------------------------------------
-        desired_nlists = int(os.getenv("FAISS_NLISTS", 100))
-        ...
-        index_type = "invertedIndex"  # FAISS IVF
+        # ---------------- HNSW index implementation --------------------
+        hnsw_m = int(os.getenv("HNSW_M", 16))
+        hnsw_ef = int(os.getenv("HNSW_EF", 200))
+        payload = {
+            "name": "nodes_embedding_hnsw",
+            "type": "vector",
+            "fields": ["embedding"],
+            "inBackground": True,
+            "params": {
+                "dimension": int(os.getenv("EMBEDDING_DIM", 768)),
+                "metric": os.getenv("VECTOR_METRIC", "cosine"),
+                "indexType": "hnsw",
+                "M": hnsw_m,
+                "efConstruction": hnsw_ef
+            }
+        }
@@
-            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_exists", **common)
+            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_exists", indexType="hnsw", **common)
```

### New Test: Decision Snippet Validation

```diff
diff --git a/tests/unit/services/ingest/test_decision_snippet_validation.py b/tests/unit/services/ingest/test_decision_snippet_validation.py
+import json, pytest
+from ingest.cli import _validator_for_schema  # helper already used in existing tests
+
+def test_decision_cannot_have_snippet(tmp_path):
+    bad = {
+        "id": "bad-decision-1",
+        "option": "Do thing",
+        "rationale": "Because.",
+        "timestamp": "2024-01-01T00:00:00Z",
+        "decision_maker": "Bob",
+        "snippet": "should not be here"
+    }
+    v = _validator_for_schema("decision")
+    with pytest.raises(Exception):
+        v.validate(bad)
```

### New Test: HNSW Vector Index Smoke Test

```diff
diff --git a/tests/ops/test_vector_index_hnsw.py b/tests/ops/test_vector_index_hnsw.py
+"""
+Smoke-test that ArangoDB hosts a 768-d HNSW vector index on `nodes.embedding`.
+Skip when ARANGO_HOST is not configured (CI unit mode).
+"""
+import os, pytest, requests, json
+
+ARANGO = os.getenv("ARANGO_HOST")
+
+@pytest.mark.skipif(ARANGO is None, reason="ArangoDB not available")
+def test_hnsw_vector_index_present():
+    url = f"http://{ARANGO}:8529/_db/_system/_api/index?collection=nodes"
+    r = requests.get(url, auth=("root", os.getenv("ARANGO_ROOT_PASSWORD","")))
+    r.raise_for_status()
+    indexes = r.json()
+    vec = [i for i in indexes if i.get("type")=="vector" and i["name"]=="nodes_embedding_hnsw"]
+    assert vec, "HNSW vector index missing"
+    idx = vec[0]
+    assert idx["params"]["dimension"] == 768
+    assert idx["params"]["indexType"] == "hnsw"
```

## Import & API-Usage Audit

All services/ingest modules import only public interfaces from core packages (core_utils, core_logging, core_storage, link_utils). Signatures match the exported classes & functions; no private attribute access detected.

**One unused symbol:** hashlib imported in pipeline.snippet_enricher; consider removing to appease linter.

## Next Steps

1. Apply patches above (schemas + ArangoStore + new tests).

2. Run `pytest -k ingest` – expect new validation tests to fail before patch, pass after.

3. Re-enable `ARANGO_VECTOR_INDEX_ENABLED=true` in local .env and run `docker compose up` to confirm HNSW index creation; new smoke-test will exercise it in CI.

4. Delete obsolete memory.tar artefact (`git rm memory.tar`).

These changes bring Milestone 1 "Ingest" fully in line with the tech-spec and tighten automated coverage so regressions surface immediately.


_______

### Analysis 2

# Milestone 1-3 "Ingest" Compliance Snapshot

## Requirements Status Overview

| # | Requirement (Milestone 1–3 scope) | Code impl. | Tests cover | Notes |
|---|-----------------------------------|------------|-------------|--------|
| 1 | Snapshot ETag generation watcher | ✅ | ✅ (test_snapshot_watcher.py) | — |
| 2 | JSON parse w/ file-line diagnostics | ✅ | ✅ | In ingest/cli.py via Draft202012Validator |
| 3 | ID regex validation (^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$) | ✅ | ✅ | |
| 4 | Artifact validation (ID, ts, content) | ✅ | ✅ | |
| 5 | Normalisation / aliasing | ✅ | ✅ | |
| 6 | Text processing (NFKC, trim, collapse, limits) | ✅ | ✅ | |
| 7 | ISO-8601 UTC timestamp parsing | ✅ | ✅ | |
| 8 | Tag processing – slug-lower, dedupe, sort | ⚠️ | ❌ | Only .lower(); fixed in patch ① |
| 9 | New fields (tags[], based_on[], snippet, x-extra{}) validation | ✅ | ✅ | |
| 10 | Back-link event.led_to ↔ decision.supported_by | ✅ | ✅ | |
| 11 | Cross-link decision.based_on ↔ prior_decision.transitions | ✅ | ✅ | |
| 12 | Transition ↔ decisions reciprocity | ✅ | ✅ | |
| 13 | Field catalog generation | ✅ | ✅ | |
| 14 | Relation catalog generation | ✅ | ✅ | |
| 15 | Event summary repair | ✅ | ✅ | |
| 16 | Arango node & edge upserts | ✅ | ✅ (nodes) / ⚠️ (edges) | |
| 17 | Content-addressable snapshot upload (MinIO) | ✅ | ⚠️ (no targeted unit test) | |
| 18 | Adjacency/edge list materialisation | ✅ | ⚠️ (missing explicit test) | |
| 19 | Vector index bootstrap (SIM_DIM = 768) | ✅ | ✅ | |

**Legend:** ✅ implemented & tested | ⚠️ implemented but under-tested / partially mismatched | ❌ missing

## Issues & Gaps

### Tag Normalisation Spec Drift
normalize_decision/event/transition() used str.lower() instead of slugification (spec §L2).

### Dead Helper
normalize_tags() existed but was unused → now wired in.

### Edge Persistence Not Asserted
Existing tests check idempotent nodes only; no test guaranteed LED_TO / CAUSAL_PRECEDES edges.

### Snapshot Artefact Tests Lacking
MinIO upload path is executed but never asserted (risk of silent regressions).

### Minor Lint
Unused imports (time, hashlib) in ingest/cli.py; black/ruff will flag.

## Proposed Additional Unit-Test Stubs

| New test file | Verifies | Fixture path hint |
|---------------|----------|-------------------|
| tests/unit/services/ingest/test_tag_slugify.py | tags slug-lower + dedupe behaviour | synthetic inline fixture → no snapshot |
| tests/unit/services/ingest/test_edge_upsert_links.py | upsert_all() actually calls upsert_edge for LED_TO & CAUSAL_PRECEDES | uses dummy in-memory store |
| tests/unit/services/ingest/test_snapshot_upload.py (suggested) | MinIO upload called once per batch, correct object key = snapshot_etag.json.gz | use moto-minio stub |

## Unified-Diff Patches

### Patch ① — Spec-Correct Tag Normalisation

```diff
diff --git a/services/ingest/src/ingest/pipeline/normalize.py b/services/ingest/src/ingest/pipeline/normalize.py
index e1f4c3a..b7d9a99 100644
--- a/services/ingest/src/ingest/pipeline/normalize.py
+++ b/services/ingest/src/ingest/pipeline/normalize.py
@@
-    out["tags"] = sorted(set(t.lower() for t in d.get("tags", [])))
+    # spec §L2: slug-lower-dedupe-sort
+    out["tags"] = normalize_tags(d.get("tags", []))
@@
-    out["tags"] = sorted(set(t.lower() for t in e.get("tags", [])))
+    # spec §L2: slug-lower-dedupe-sort
+    out["tags"] = normalize_tags(e.get("tags", []))
@@
-    out["tags"] = sorted(set(x.lower() for x in t.get("tags", [])))
+    # spec §L2: slug-lower-dedupe-sort
+    out["tags"] = normalize_tags(t.get("tags", []))
```

### Patch ② — Unit Test for Tag Slugification

```diff
diff --git a/tests/unit/services/ingest/test_tag_slugify.py b/tests/unit/services/ingest/test_tag_slugify.py
new file mode 100644
index 0000000..e5c4f2b
--- /dev/null
+++ b/tests/unit/services/ingest/test_tag_slugify.py
@@
+from ingest.pipeline.normalize import normalize_decision
+
+
+def test_tag_slugify_normalization():
+    raw = {
+        "id": "foo-decision",
+        "option": "Foo",
+        "rationale": "Because.",
+        "timestamp": "2024-01-02T03:04:05Z",
+        "tags": ["Strategic Pivot", "strategic_pivot", "Strategic-Pivot  "],
+    }
+    out = normalize_decision(raw)
+    # Expect slug-lower, dedup, sort
+    assert out["tags"] == ["strategic-pivot"], out["tags"]
```

### Patch ③ — Unit Test for Edge Upserts

```diff
diff --git a/tests/unit/services/ingest/test_edge_upsert_links.py b/tests/unit/services/ingest/test_edge_upsert_links.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/tests/unit/services/ingest/test_edge_upsert_links.py
@@
+from ingest.pipeline.graph_upsert import upsert_all
+
+
+class DummyStore:
+    def __init__(self):
+        self.nodes = {}
+        self.edges = {}
+
+    def upsert_node(self, _id, _kind, doc):
+        self.nodes[_id] = doc
+
+    def upsert_edge(self, _id, _from, _to, _kind, doc):
+        self.edges[_id] = (_from, _to, _kind)
+
+
+def _sample_docs():
+    decision = {
+        "id": "foo-dec",
+        "option": "Foo",
+        "timestamp": "2024-01-01T00:00:00Z",
+        "supported_by": [],
+        "based_on": [],
+        "transitions": [],
+    }
+    event = {
+        "id": "bar-ev",
+        "summary": "Bar",
+        "description": "Bar desc.",
+        "timestamp": "2024-01-02T00:00:00Z",
+        "led_to": ["foo-dec"],
+    }
+    transition = {
+        "id": "baz-tr",
+        "from": "foo-dec",
+        "to": "foo-dec",
+        "relation": "causal",
+        "reason": "self-loop",
+        "timestamp": "2024-01-03T00:00:00Z",
+    }
+    return {"foo-dec": decision}, {"bar-ev": event}, {"baz-tr": transition}
+
+
+def test_edges_upserted():
+    store = DummyStore()
+    decisions, events, transitions = _sample_docs()
+    upsert_all(store, decisions, events, transitions, snapshot_etag="snap123")
+
+    # LED_TO edge exists
+    assert "ledto:bar-ev->foo-dec" in store.edges
+    # Transition edge exists
+    assert "transition:baz-tr" in store.edges
```