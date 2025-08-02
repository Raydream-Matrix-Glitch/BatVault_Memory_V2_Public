### The doc provides three analysis that need validation

## Analysis 1

Spec / Mapping requirement	Source directory (batvault_live_snapshot)	Status
Evidence builder, un-bounded k = 1 collect	services/gateway/src/gateway/evidence.py	Found – gets full events, preceding, succeeding from /api/graph/expand_candidates; no in-code limits.
Size-aware selector + weak-AI scorer	services/gateway/src/gateway/selector.py	Found – deterministic recency + similarity scorer, truncates when bundle_size_bytes > MAX_PROMPT_BYTES.
Validator enforcing WhyDecisionAnswer@1	packages/core_validator/src/core_validator/validator.py	Found – schema+ID checks, but length & allowed_ids-union checks missing.
Deterministic templater fallback	services/gateway/src/gateway/templater.py	Found – constant-shape short-answer (≤320 chars).
Canonical prompt envelope + SHA-256 FP	services/gateway/src/gateway/prompt_envelope.py + packages/core_utils/src/core_utils/fingerprints.py	Found – canonical JSON & fingerprints, but prefix sha256: missing.
Artifact persistence (envelope, pre/post evidence, raw-LLM, validator)	services/gateway/src/gateway/app.py → _minio_put_batch()	Found – all artefacts written to MinIO bucket.

(requirements: Tech spec §B2–B5, §C tech-spec | Milestone-3 map requirements_to_milesto…)

✔️ What works
Un-bounded collect → size-based truncate is honoured: builder gathers all neighbours, selector prunes only when bundle_size_bytes > MAX_PROMPT_BYTES (= 8192).
Deterministic fallback chain (validate_response ➜ validate_and_fix ➜ templater) is wired and captured in meta.fallback_used.
Structured metrics & OTEL spans: selector emits total_neighbors_found, bundle_size_bytes, etc.; evidence builder attaches selector meta to the span.
All artefacts are put to S3/MinIO under request_id/….

❌ Gaps & errors discovered
ID	Finding	Impact
G-1	Validator misses length limits (short_answer ≤ 320, rationale_note ≤ 280).	Oversized LLM output passes; golden tests for size caps would fail.
G-2	Validator does not enforce allowed_ids = anchor ∪ events ∪ transitions (only checks supporting_ids ⊆ allowed_ids).	Contract breach – downstream tooling cannot rely on allowed_ids.
G-3	Selector truncation ignores transitions. Only events are scored/dropped; large preceding/succeeding lists can still overflow prompt budget.	
G-4	selector.py fast-path recalculates allowed_ids from events only (anchor/transition IDs omitted). This is later patched in /v2/ask, but other call-sites (e.g. future /chains) will be wrong.	
G-5	_sha256() in prompt_envelope.py returns bare hex; spec requires sha256:… prefix (§C-2).	Fingerprint format mismatch breaks replay tooling that parses prefix.
G-6	No unit tests cover transition-drop path or length-limit validation – edge cases unguarded.	

🛠️ Patch set (git-style diffs)

diff --git a/packages/core_validator/src/core_validator/validator.py b/packages/core_validator/src/core_validator/validator.py
@@
-    errs: List[str] = []
+    errs: List[str] = []
+
+    # --- WhyDecisionAnswer@1 length limits (spec §F1) --------------------
+    if len(resp.answer.short_answer or "") > 320:
+        errs.append("short_answer exceeds 320 characters")
+    if resp.answer.rationale_note and len(resp.answer.rationale_note) > 280:
+        errs.append("rationale_note exceeds 280 characters")
@@
-    # transitions cited
+    # transitions cited
@@
-    if cf.has_succeeding != bool(resp.evidence.transitions.succeeding):
+    if cf.has_succeeding != bool(resp.evidence.transitions.succeeding):
         errs.append("completeness_flags.has_succeeding mismatch")
+
+    # --- allowed_ids exact union (Milestone-3 mapping) -------------------
+    expected = {resp.evidence.anchor.id}
+    expected |= {e.get("id") for e in resp.evidence.events if isinstance(e, dict) and e.get("id")}
+    expected |= {t.get("id") for t in (resp.evidence.transitions.preceding +
+                                       resp.evidence.transitions.succeeding)
+                 if isinstance(t, dict) and t.get("id")}
+    if set(resp.evidence.allowed_ids) != expected:
+        errs.append("allowed_ids mismatch union of anchor, events and transitions")
 
     return (not errs), errs

diff --git a/services/gateway/src/gateway/prompt_envelope.py b/services/gateway/src/gateway/prompt_envelope.py
@@
-def _sha256(data: bytes) -> str:
-    return hashlib.sha256(data).hexdigest()
+def _sha256(data: bytes) -> str:
+    # Prefix required by spec §C-2 (“sha256:<hex>”)
+    return "sha256:" + hashlib.sha256(data).hexdigest()


diff --git a/services/gateway/src/gateway/selector.py b/services/gateway/src/gateway/selector.py
@@
-    raw_size = bundle_size_bytes(ev)
-    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
-        ev.allowed_ids = sorted(x.get("id") for x in ev.events if x.get("id"))
+    raw_size = bundle_size_bytes(ev)
+    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
+        # ensure anchor + transition IDs included
+        ev.allowed_ids = sorted(
+            {ev.anchor.id}
+            | {e.get("id") for e in ev.events if e.get("id")}
+            | {t.get("id") for t in (ev.transitions.preceding + ev.transitions.succeeding) if t.get("id")}
+        )
@@
-    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor))
-    ev.events = events_sorted.copy()
-    while bundle_size_bytes(ev) > MAX_PROMPT_BYTES and len(ev.events) > MIN_EVIDENCE_ITEMS:
-        ev.events.pop()
+    # unified candidate list (events + transitions) for pruning
+    candidates = (
+        [("event", e) for e in ev.events] +
+        [("preceding", t) for t in ev.transitions.preceding] +
+        [("succeeding", t) for t in ev.transitions.succeeding]
+    )
+    candidates.sort(key=lambda p: _score(p[1], ev.anchor))
+
+    def _drop(kind: str, item):
+        if kind == "event":
+            ev.events.remove(item)
+        elif kind == "preceding":
+            ev.transitions.preceding.remove(item)
+        else:
+            ev.transitions.succeeding.remove(item)
+
+    while bundle_size_bytes(ev) > MAX_PROMPT_BYTES and \
+          (len(ev.events) + len(ev.transitions.preceding) + len(ev.transitions.succeeding)) > MIN_EVIDENCE_ITEMS:
+        _drop(*candidates.pop())   # drop worst-scored item
@@
-    kept_ids   = {x.get("id") for x in ev.events if x.get("id")}
-    dropped    = [x.get("id") for x in events_sorted if x.get("id") and x.get("id") not in kept_ids]
+    kept_ids = (
+        {e.get("id") for e in ev.events if e.get("id")} |
+        {t.get("id") for t in (ev.transitions.preceding + ev.transitions.succeeding) if t.get("id")}
+    )
+    dropped = [cid for cid in ev.allowed_ids if cid not in kept_ids]
 
     ev.allowed_ids = sorted(kept_ids)



## Analysis 2

✅ Milestone-3 discovery map
Gateway component	Location in snapshot	Relevant spec clauses	Notes
Evidence Builder	services/gateway/src/gateway/evidence.py	§ B2, B2.1, M4	k = 1 plan is un-bounded ✔︎; relies on Memory-API expand endpoint
Selector model + truncator	services/gateway/src/gateway/selector.py	§ B2.1 “Weak AI”, § M4 size constants	Deterministic recency + similarity baseline implemented ✔︎
Prompt-envelope builder	services/gateway/src/gateway/prompt_envelope.py	§ C.1-C.3	Canonical JSON + SHA-256 fingerprints ✔︎
Validator	packages/core_validator/src/core_validator/validator.py	§ B4 rules matrix	Enforces schema, ID-scope, mandatory citations ✔︎
Deterministic templater	services/gateway/src/gateway/templater.py	§ B3 fallback rules	Used after LLM/validator failure ✔︎
Artifact persistence	_minio_put_batch() in gateway/app.py	§ B5 audit & § O retention	Writes envelope / prompt / raw-LLM / validator-report / final-JSON ✔︎

🔎 Functionality & correctness checks
Requirement (mapping doc → spec)	Result	Evidence
Un-bounded k = 1 collection (M3-Req 1, spec § B2)	Pass	Planner posts {id, k:1} without limit.
Truncation only when > MAX_PROMPT_BYTES (M3-Req 1, § M4)	Pass (soft-threshold 6144, hard 8192)	truncate_evidence() logic matches spec update.
Selector: model + deterministic fallback (M3-Req 2, § B2.1)	Pass	GBDT placeholder missing (OK for baseline); recency/-sim scoring present.
Validator schema & ID-scope (M3-Req 3, § B4)	Pass	All 4 rule blocks present.
Prompt envelope canonical JSON + fingerprint (M3-Req 4, § C.1-C.2)	Pass	canonical_json() → SHA-256; fingerprints promoted to OTEL span.
Artifact persistence of 5 objects (M3-Req 5, § B5)	Pass	put_object() for each artifact; bucket autocreation via ensure_bucket().

❌ Gaps & logical flaws
ID	Severity	Component	Issue
G-1	🟥 Blocker	Evidence Builder / Selector	allowed_ids omits anchor & transition IDs and is absent at instantiation, raising a ValidationError (violates § B2 “exact union” & breaks downstream validator).
G-2	🟨 Minor	Selector metrics	total_neighbors_found counts only events, not preceding/succeeding transitions (§ B5 logging table).
G-3	🟨 Minor	Selector meta	Non-truncation path logs selector_truncation: False but omits dropped_evidence_ids key (spec requires field even if empty).
G-4	🟦 Nice-to-have	Validator	Does not enforce rationale_note ≤ 280 chars (tech-spec § F1).
G-5	🟦 Nice-to-have	Evidence metrics	No OTEL histogram for final_evidence_count when no truncation (spec § B5).

💡 Patch set (git-style diffs)
1️⃣ Fix blocker G-1 + metrics gaps G-2 / G-3

--- a/services/gateway/src/gateway/evidence.py
+++ b/services/gateway/src/gateway/evidence.py
@@
-        ev = WhyDecisionEvidence(
-            anchor=anchor,
-            events=events,
-            transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
-        )
+        # ---------------------------------------------------------------- #
+        # Build *complete* allowed_ids per spec § B2 (anchor ∪ events ∪ trans)
+        # ---------------------------------------------------------------- #
+        def _collect_allowed_ids() -> list[str]:
+            ids: set[str] = {anchor.id}
+            ids.update([e.get("id") for e in events if isinstance(e, dict)])
+            ids.update([t.get("id") for t in trans_pre + trans_suc if isinstance(t, dict)])
+            return sorted([i for i in ids if i])
+
+        ev = WhyDecisionEvidence(
+            anchor=anchor,
+            events=events,
+            transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
+            allowed_ids=_collect_allowed_ids(),
+        )
 
@@
-            ev, selector_meta = truncate_evidence(ev)
+            ev, selector_meta = truncate_evidence(ev)
             ev.__dict__["_selector_meta"] = selector_meta
+            # Log selector meta on bundle span for full observability (§ B5)
+            for k, v in selector_meta.items():
+                span.set_attribute(k, v)
 

--- a/services/gateway/src/gateway/selector.py
+++ b/services/gateway/src/gateway/selector.py
@@
-from core_models.models import (
-     WhyDecisionResponse,
-     WhyDecisionAnswer,
-     WhyDecisionAnchor,
-)
+# Local import kept minimal to avoid heavy deps at import-time
+from core_models.models import WhyDecisionAnchor, WhyDecisionEvidence
 from core_config.constants import (
     MAX_PROMPT_BYTES,
     SELECTOR_TRUNCATION_THRESHOLD,
     MIN_EVIDENCE_ITEMS,
     SELECTOR_MODEL_ID,
 )
@@
-def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
+def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
     return len(orjson.dumps(ev.model_dump(mode="python")))
 
+# ------------------------------------------------------------------ #
+#   helpers                                                          #
+# ------------------------------------------------------------------ #
+def _union_ids(ev: WhyDecisionEvidence) -> list[str]:
+    """anchor ∪ events ∪ transitions (spec § B2)."""
+    ids = {ev.anchor.id}
+    ids.update([e.get("id") for e in ev.events if isinstance(e, dict)])
+    ids.update(
+        [t.get("id") for t in ev.transitions.preceding + ev.transitions.succeeding
+         if isinstance(t, dict)]
+    )
+    return sorted([x for x in ids if x])
@@
-    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
-        ev.allowed_ids = sorted(x.get("id") for x in ev.events if x.get("id"))
+    if raw_size <= SELECTOR_TRUNCATION_THRESHOLD:
+        ev.allowed_ids = _union_ids(ev)
         meta = {
             "selector_truncation": False,
-            "total_neighbors_found": len(ev.events),
-            "final_evidence_count": len(ev.events),
-            "dropped_evidence_ids": [],
+            "total_neighbors_found": len(ev.events)
+               + len(ev.transitions.preceding)
+               + len(ev.transitions.succeeding),
+            "final_evidence_count": len(ev.allowed_ids),
+            "dropped_evidence_ids": [],
             "bundle_size_bytes": raw_size,
             "max_prompt_bytes": MAX_PROMPT_BYTES,
             "selector_model_id": SELECTOR_MODEL_ID,
         }
@@
-    kept_ids   = {x.get("id") for x in ev.events if x.get("id")}
+    kept_ids   = set(_union_ids(ev))
     dropped    = [x.get("id") for x in events_sorted if x.get("id") and x.get("id") not in kept_ids]
-    ev.allowed_ids = sorted(kept_ids)
+    ev.allowed_ids = sorted(kept_ids)
 
     meta = {
         "selector_truncation": len(dropped) > 0,
-        "total_neighbors_found": len(events_sorted),
-        "final_evidence_count": len(ev.events),
+        "total_neighbors_found": len(events_sorted)
+           + len(ev.transitions.preceding)
+           + len(ev.transitions.succeeding),
+        "final_evidence_count": len(ev.allowed_ids),
         "dropped_evidence_ids": dropped,
         "bundle_size_bytes": final_size,
         "max_prompt_bytes": MAX_PROMPT_BYTES,
         "selector_model_id": SELECTOR_MODEL_ID,
     }

2️⃣ Optional guard (G-4)
--- a/packages/core_validator/src/core_validator/validator.py
@@
     try:
         WhyDecisionResponse.model_validate(resp.model_dump(mode="python"))
     except Exception as exc:
         errs.append(f"response schema error: {exc}")
+    # rationale_note ≤ 280 chars (spec § F1)
+    if resp.answer.rationale_note and len(resp.answer.rationale_note) > 280:
+        errs.append("rationale_note exceeds 280-char limit")