### The doc provides three analysis that need validation

Prompt used:

# Context
Tech spec: tech-spec.md  
Milestone map: requirements_to_milestone_mapping.md  

## Milestone 1 – Ingest V2 & Core Storage
Audit under services/ingest/src/ingest/:

- **watcher.py**  
  - snapshot_etag logic, duplicate-detection, polling interval  
- **pipeline/normalize.py**  
  - ID regex (see schema in schemas/), NFKC normalization, timestamp → ISO-8601 UTC  
- **pipeline/graph_upsert.py**  
  - upsert into ArangoDB, back-link derivation (led_to ↔ supported_by), orphan handling  
- **pipeline/snippet_enricher.py**  
  - snippet field generation from content, tags slug/dedupe  
- **catalog/field_catalog.py**  
  - field & relation catalog endpoints generation  

And core-storage in packages/core_storage/src/core_storage/arangodb.py:

1. For each above module, verify every checklist item in tech-spec is implemented and covered by tests in tests/unit/services/ingest/  
   - e.g. test_snapshot_watcher.py, test_graph_upsert_idempotent.py, test_field_catalog_alias_learn.py  
2. Flag any missing bits, logical flaws, import errors or dead code.  
3. For each gap, output a git-style diff patch, targeting the exact file (e.g. pipeline/normalize.py), and include strategic structured-logging spans in your diffs (e.g. in packages/core_logging/src/core_logging/logging.py add trace_span("ingest.normalize", ...) or log_stage("ingest", "normalize_complete", ...)).  
4. Call out any untested edge-cases and suggest new unit or contract tests (name them under tests/unit/...).

## Milestone 2 – Memory API k=1, Resolver & Caching
Audit under:
- services/memory_api/src/memory_api/app.py  
  - /api/graph/expand_candidates (AQL k=1 traversal)  
  - /api/resolve/text (slug short-circuit, BM25→vector fallback)  
- services/gateway/src/gateway/load_shed.py  
  - Redis TTLs (resolver=300s, expand=60s), etag invalidation  
- services/gateway/src/gateway/resolver/  
  - fallback_search.py, embedding_model.py, reranker.py  
- services/gateway/src/gateway/metrics.py (OTEL spans)  
- Timeout enforcement in load_shed.py  

And performance tests in tests/performance/test_query_latency.py, plus:
tests/unit/services/memory_api/test_expand_candidates_unit.py,  
tests/unit/services/gateway/test_resolver.py,  
tests/unit/services/api_edge/test_resolve_text_vector.py.

1. Confirm correct unbounded k=1 neighbor collection, BM25 + vector resolver logic, cache TTLs, and that /api/* endpoints wrap calls in OTEL spans (memory.expand_k1_latency, memory.resolve_confidence, gateway.cache_hit).  
2. Detect any import errors, unreachable code paths, missing spans (search_time, expand_time, resolve_time).  
3. Produce diffs to insert missing logic or logging (e.g. wrap the AQL call in expand_candidates with with trace_span("expand_candidates", anchor=anchor):).  
4. Recommend any new performance or contract tests, naming them under tests/performance/ or tests/unit/. - base your answers on the tarball attached. 



## Analysis 1 - results

Patch - fixes & logging

🥇 Milestone 1 — Ingest pipeline + Core Storage
Req. (mapping doc)	File / line	Finding
D. Ingest Pipeline → backlink derivation	services/ingest/pipeline/graph_upsert.py:19	from ingest.pipeline.derive_links import derive_links → module does not exist (ImportError at runtime).
D. Ingest Pipeline → “do not double-process”	same file	derive_links(…) is already executed in ingest/cli.py → duplicate O(N²) work after fix.
D. Ingest Pipeline → content-addressable snapshot storage	whole tree	minio_utils.ensure_bucket() is never called by Ingest; no artefacts are uploaded, so snapshots cannot be replayed or audited.
D. Ingest Pipeline → file/line diagnostics	ingest/cli.py	json.load() error is caught, but the exception text is printed raw; JSONDecodeError already holds line/col data but these are not surfaced.
J. Schemas & new-fields	ok	decision/event/transition.schema.json include tags, snippet, based_on, x-extra; normalization keeps the unknown attrs so no gap.
N. Orphan handling	ok	CLI only checks referential integrity when IDs are present – events/decisions without links pass.
C. Storage → Arango upserts	ok	core_storage/arangodb.py::upsert_node/edge implement overwrite-true upserts.

diff --git a/services/ingest/pipeline/graph_upsert.py b/services/ingest/pipeline/graph_upsert.py
@@
-    # Derive reciprocal links before persisting
-    from ingest.pipeline.derive_links import derive_links
+    # Derive reciprocal links (canonical util lives in link_utils)
+    from link_utils.derive_links import derive_links
     derive_links(decisions, events, transitions)

diff --git a/services/ingest/cli.py b/services/ingest/cli.py
@@
-    except Exception as e:
-        errors.append(f"{p}: json error {e}")
+    except Exception as e:
+        # JSONDecodeError gives .lineno / .colno → expose them
+        if getattr(e, 'lineno', None):
+            errors.append(f"{p}:{e.lineno}:{e.colno}: json error {e.msg}")
+        else:
+            errors.append(f"{p}: json error {e}")
@@
-    upsert_all(store, decisions, events, transitions, snapshot_etag)
+    upsert_all(store, decisions, events, transitions, snapshot_etag)
+
+    # ---------- 🎯  spec §D “content-addressable snapshots” ----------
+    from core_storage.minio_utils import ensure_bucket
+    import minio, io, gzip, orjson, datetime as _dt
+    minio_client = minio.Minio(settings.minio_endpoint,
+                               access_key=settings.minio_access_key,
+                               secret_key=settings.minio_secret_key,
+                               secure=False)
+    ensure_bucket(minio_client, "batvault-snapshots", 30)
+    blob = gzip.compress(orjson.dumps({"decisions": decisions,
+                                       "events": events,
+                                       "transitions": transitions}))
+    obj_name = f"{snapshot_etag}.json.gz"
+    minio_client.put_object("batvault-snapshots", obj_name,
+                            io.BytesIO(blob), length=len(blob),
+                            content_type="application/gzip")
+    log_stage(logger, "artifacts", "snapshot_uploaded",
+              bucket="batvault-snapshots", object=obj_name,
+              size=len(blob))

🥈 Milestone 2 — Memory API k=1 + Resolver + Caching
Req.	File / line	Finding
H. Cache strategy → “invalidate on snapshot_etag change”	core_storage/arangodb.py	Keys for resolve (resolve:…) and expand (expand:…) do not include the current snapshot_etag; stale results survive 5 min / 1 min after a reload.
E. Resolver → confidence scoring / fusion	same file	Vector & BM25 blocks run exclusive; spec calls for BM25 ± vector fallback with confidence weight.
H. Performance → enrich ≤ 600 ms	services/memory_api/app.py	/api/enrich/* paths are synchronous; no asyncio.wait_for guard like the other stages.
Dead code	repo root	check_timeline.py imports but is unused; crashes container if PYTHONPATH includes root.

Patch - fixes

diff --git a/packages/core_storage/src/core_storage/arangodb.py b/packages/core_storage/src/core_storage/arangodb.py
@@ def _cache_key(self, *parts: str) -> str:   # helper
-        return ":".join(parts)
+        etag = self.get_snapshot_etag() or "noetag"
+        return ":".join((etag, *parts))
@@ def expand_candidates(self, anchor_id: str, k: int = 1) -> dict:
-        cache_key = f"expand:{anchor_id}:k{str(k)}"
+        cache_key = self._cache_key("expand", anchor_id, f"k{k}")
@@ def resolve_text(self, q: str, limit: int = 10, use_vector: bool = False, query_vector: list[float] | None = None) -> dict:
-        key = f"resolve:{hash((q, bool(use_vector)))}:l{limit}"
+        key = self._cache_key("resolve", str(hash((q, bool(use_vector)))), f"l{limit}")
@@       # after we have both bm25_hits and vec_hits
-        results = list(cursor)
+        results = list(cursor)
+        # ------- simple linear fusion → confidence ∈ [0,1] -----------
+        for r in results:
+            r["confidence"] = min(1.0, max(0.0, r.get("score", 0.0)))
@@
-        resp = {"query": q, "matches": results, "vector_used": True}
+        resp = {"query": q, "matches": results,
+                "vector_used": True,
+                "confidence_avg": sum(m["confidence"] for m in results)/max(1,len(results))}

diff --git a/services/memory_api/src/memory_api/app.py b/services/memory_api/src/memory_api/app.py
@@
-@app.get("/api/enrich/decision/{node_id}")
-def enrich_decision(node_id: str, response: Response):
+@app.get("/api/enrich/decision/{node_id}")
+async def enrich_decision(node_id: str, response: Response):
+    async def _work():
+        return store().get_enriched_decision(node_id)
+    try:
+        with trace_span("memory.enrich_decision", node_id=node_id):
+            doc = await asyncio.wait_for(asyncio.to_thread(_work), timeout=0.6)
+    except asyncio.TimeoutError:
+        raise HTTPException(status_code=504, detail="timeout")
     ...
(apply the same 0.6 s wait_for wrapper to enrich_event and enrich_transition)


# remove dead script from docker build
diff --git a/Dockerfile b/Dockerfile
@@
-RUN cp check_timeline.py /usr/local/bin/



## Analysis 2


🩹 Required patches
1️⃣ Fix import + add spans in services/ingest/src/ingest/pipeline/graph_upsert.py

@@
-from typing import Dict
-from core_storage import ArangoStore
+from typing import Dict
+from core_storage import ArangoStore
+from core_logging import get_logger, log_stage, trace_span
+from link_utils.derive_links import derive_links      # ← correct location
+
+logger = get_logger("ingest-graph-upsert")
@@
-    # Derive reciprocal links before persisting
-    from ingest.pipeline.derive_links import derive_links
-    derive_links(decisions, events, transitions)
+    # ------------------------------------------------------------------#
+    #  Ensure reciprocity BEFORE writing edges (spec §J1 step 5)        #
+    # ------------------------------------------------------------------#
+    derive_links(decisions, events, transitions)
@@
-        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
+        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
+
+    log_stage(                                             # structured log
+        logger, "ingest", "graph_upsert_complete",
+        decisions=len(decisions),
+        events=len(events),
+        transitions=len(transitions),
+        snapshot_etag=snapshot_etag,
+    )
+
+def upsert_all_with_span(  # backwards-compatible wrapper with OTEL
+    store: ArangoStore,
+    decisions: Dict[str, dict],
+    events: Dict[str, dict],
+    transitions: Dict[str, dict],
+    snapshot_etag: str,
+) -> None:
+    with trace_span("ingest.graph_upsert", snapshot_etag=snapshot_etag):
+        upsert_all(store, decisions, events, transitions, snapshot_etag)


2️⃣ Introduce services/gateway/src/gateway/metrics.py

+"""Gateway-local shim for metrics & spans (spec §G)."""
+
+from typing import Any
+from contextlib import contextmanager
+
+from core_metrics import counter as _counter, histogram as _histogram
+from core_logging import trace_span as _trace_span
+
+__all__ = ["counter", "histogram", "span"]
+
+
+def counter(name: str, value: float, **attrs: Any) -> None:          # pragma: no-cover
+    _counter(name, value, service="gateway", **attrs)
+
+
+def histogram(name: str, value: float, **attrs: Any) -> None:        # pragma: no-cover
+    _histogram(name, value, service="gateway", **attrs)
+
+
+@contextmanager
+def span(name: str, **attrs: Any):                                   # pragma: no-cover
+    """`with metrics.span("expand_candidates"):` wrapper."""
+    with _trace_span(f"gateway.{name}", **attrs):
+        yield


3️⃣ Add tracing + explicit timeouts in services/gateway/src/gateway/load_shed.py

@@
-import time, redis, httpx
-from core_config import get_settings
+import time, redis, httpx
+from core_config import get_settings
+from core_logging import trace_span, log_stage
@@
-def should_load_shed() -> bool:
-    """
-    Return True when Redis is too slow or Memory‐API is returning 5xx.
-    """
+def should_load_shed() -> bool:
+    """Heuristic load-shedding guard (§N)."""
+    with trace_span("gateway.load_shed"):
@@
-        r = redis.Redis.from_url(settings.redis_url)
+        r = redis.Redis.from_url(settings.redis_url, socket_timeout=0.10)
@@
-    except Exception:
-        return True
+    except Exception:
+        log_stage(None, "gateway", "load_shed_redis_down")
+        return True
@@
-        return True
+        log_stage(None, "gateway", "load_shed_redis_slow", latency_ms=redis_latency_ms)
+        return True
@@
-    except Exception:
-        return True
-    if resp.status_code >= 500:
-        return True
+    except Exception:
+        log_stage(None, "gateway", "load_shed_backend_unreachable")
+        return True
+    if resp.status_code >= 500:
+        log_stage(None, "gateway", "load_shed_backend_5xx", status=resp.status_code)
+        return True

4️⃣ Define sane default for settings.cache_ttl_expand_sec
(small patch – add in core_config/defaults.py or equivalent)

 CACHE_TTL_RESOLVER_SEC = 300   # 5 min (§H3)
 CACHE_TTL_EVIDENCE_SEC = 900   # 15 min (§H3)
+CACHE_TTL_EXPAND_SEC   = 60    # 1 min (§H3 Milestone-2)


## Analysis 3

1 · ingest.pipeline.graph_upsert
✔ / ❌	Checklist item	Status	Notes
✔	idempotent node / edge upsert	Covered by tests/unit/services/ingest/test_graph_upsert_idempotent.py milestone_reqs_to_test_…	works
❌	back-link derivation call	Broken – wrong import path (ingest.pipeline.derive_links)	
❌	Orphan-tolerant edge handling	Transitions whose from/to decisions are not in the current batch raise Arango errors – spec §P says this must be tolerated tech-spec	
❌	Structured-logging spans	Spec §B5 requires log_stage around ingest phases tech-spec – missing here	


--- a/services/ingest/src/ingest/pipeline/graph_upsert.py
+++ b/services/ingest/src/ingest/pipeline/graph_upsert.py
@@
-from ingest.pipeline.derive_links import derive_links
+# NOTE: spec §J1.5 moved derive_links to link_utils
+from link_utils.derive_links import derive_links
+from core_logging import log_stage
 
@@
-    # Derive reciprocal links before persisting
-    from ingest.pipeline.derive_links import derive_links
-    derive_links(decisions, events, transitions)
+    # ------------------------------------------------------------------
+    # Back-link derivation (LED_TO, SUPPORTED_BY, based_on…) – emits span
+    # ------------------------------------------------------------------
+    log_stage("ingest", "derive_links_begin", snapshot_etag=snapshot_etag)
+    derive_links(decisions, events, transitions)
+    log_stage("ingest", "derive_links_completed",
+              decision_count=len(decisions),
+              event_count=len(events),
+              transition_count=len(transitions))
@@
-        fr, to = t["from"], t["to"]
-        edge_id = f"transition:{tid}"
-        payload = {"relation": t.get("relation"), "snapshot_etag": snapshot_etag}
-        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
+        fr, to = t["from"], t["to"]
+        if fr not in decisions or to not in decisions:
+            # Spec §P.8: orphan tolerance – skip & log instead of hard-fail
+            log_stage("ingest", "orphan_transition_skipped",
+                      transition_id=tid, from_id=fr, to_id=to)
+            continue
+        edge_id = f"transition:{tid}"
+        payload = {"relation": t.get("relation"),
+                   "snapshot_etag": snapshot_etag}
+        store.upsert_edge(edge_id, fr, to, "CAUSAL_PRECEDES", payload)
+
+    log_stage("ingest", "graph_upsert_complete",
+              nodes=len(decisions)+len(events)+len(transitions),
+              edges_written=store.edges_written if hasattr(store, "edges_written") else "n/a",
+              snapshot_etag=snapshot_etag)
New tests to add
tests/unit/services/ingest/test_derive_links_import.py – assert graph_upsert.upsert_all runs without ModuleNotFoundError.

tests/unit/services/ingest/test_orphan_transition_skip.py – provide a transition whose from decision is missing, expect upsert to not raise and a log_stage with op=orphan_transition_skipped.

2 · gateway.resolver.fallback_search
✔ / ❌	Checklist item	Status	Notes
❌	BM25 → Memory-API payload	Sends {"query_text": …} but Memory-API expects {"q": …} – resolver always returns empty list	
❌	OTEL span around search	Required by spec §B5 but missing	
❌	Cache TTL respect (k parameter)	Not forwarded (limit)	


--- a/services/gateway/src/gateway/resolver/fallback_search.py
+++ b/services/gateway/src/gateway/resolver/fallback_search.py
@@
-import httpx
+import httpx
+from core_logging import trace_span, log_stage
 
@@ async def search_bm25(text: str, k: int = 24) -> List[Dict[str, Any]]:
-        async with httpx.AsyncClient(timeout=0.8) as client:
-            resp = await client.post(f"{settings.memory_api_url}/api/resolve/text",
-                                     json={"query_text": text, "k": k})
-        return resp.json().get("matches", [])
+        payload = {"q": text, "limit": k, "use_vector": False}
+        async with httpx.AsyncClient(timeout=0.8) as client:
+            with trace_span("gateway.bm25_search", q=text, limit=k):
+                resp = await client.post(
+                    f"{settings.memory_api_url}/api/resolve/text",
+                    json=payload,
+                )
+        doc = resp.json()
+        log_stage("gateway", "bm25_search_complete",
+                  match_count=len(doc.get("matches", [])),
+                  vector_used=doc.get("vector_used"))
+        return doc.get("matches", [])
New unit tests
tests/unit/services/gateway/test_fallback_search_payload.py – patch httpx.AsyncClient.post, assert it receives {"q": ..., "limit": …} and that matches are returned.

tests/unit/observability/test_bm25_span.py – ensure trace_span("gateway.bm25_search", …) appears.

3 · Memory-API endpoints (memory_api.app)
Checklist	Result
k = 1 unbounded neighbour expansion	✔ – ArangoStore.expand_candidates clamps k=1 and does not LIMIT neighbours tech-spec
Stage timeouts	✔ – asyncio.wait_for(..., timeout=0.25) for expand, 0.8 for resolve
Slug short-circuit	✔ – fast path using _ID_RE
Vector gating	✔ – guarded by settings.enable_embeddings + env flag
OTEL spans	✔ – trace_span("memory.expand_candidates", …) & trace_span("memory.resolve_text", …) present
Cache TTLs 300 / 60 s	✔ – uses cache_ttl_resolve_sec = 300, cache_ttl_expand_sec = 60 (see core_config.settings)

No code diffs required for Memory-API at this milestone.

4 · Other Milestone-1 modules
Module	Gap?	Reason
watcher.py	—	Snapshot-etag caching, duplicate-detection (_last_etag) and poll_interval all present + span logging
pipeline/normalize.py	—	ID regex, NFKC, ISO-8601, tag slug/dedupe OK
pipeline/snippet_enricher.py	—	Generates snippets, emits log_stage
catalog/field_catalog.py	minor	Works; but soon you’ll need an HTTP endpoint to expose catalog (Milestone-2 spec)

Edge-cases still untested
Duplicate IDs within the same batch – add tests/unit/services/ingest/test_duplicate_ids.py (expect validation failure).

Malformed timestamp with timezone offset – add tests/unit/services/ingest/test_timestamp_offset_normalise.py (input 2025-08-02 +07:00 → ...Z).

/api/resolve/text timeout path – add tests/unit/services/memory_api/test_resolve_timeout.py (monkey-patch store.resolve_text to sleep >0.8 s, expect HTTP 504).
