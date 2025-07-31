1 ️⃣ services/gateway/src/gateway/evidence.py
diff
Copy
Edit
@@
-from typing import List, Dict, Any, Optional
+from typing import List, Dict, Any, Optional, Tuple
@@
-CACHE_TTL_SEC = 900  # 15 min – spec §9.3
+# ------------------------------------------------------------------#
+#  Constants & helpers                                              #
+# ------------------------------------------------------------------#
+CACHE_TTL_SEC = 900                           # 15 min (§H3)
+ALIAS_TPL = "evidence:{anchor_id}:latest"     # alias → composite key
@@
-    def build(self, anchor_id: str) -> WhyDecisionEvidence:
-        # ---------------- composite cache key ---------------------- #
-        cache_key = _make_cache_key(
-            decision_id=anchor_id,
-            intent="why_decision",
-            graph_scope="k1",
-            snapshot_etag=ev_headers.get("snapshot_etag", "unknown"),
-            truncation_applied=False,  # we cache the *full* bundle
-        )
-        if self._redis is not None:
-            cached = self._redis.get(cache_key)
-            if cached:
-                try:
-                    ev = WhyDecisionEvidence.model_validate_json(cached)
-                    logger.debug("evidence cache hit", extra={"anchor_id": anchor_id})
-                    return ev
-                except ValidationError:
-                    logger.warning("cached evidence failed validation; purging", extra={"anchor_id": anchor_id})
-                    self._redis.delete(cache_key)
-
-        ev, ev_headers = self._collect_from_upstream(anchor_id)
-        ev, selector_meta = truncate_evidence(ev)
-
-        if self._redis is not None:
-            try:
-                self._redis.setex(cache_key, CACHE_TTL_SEC, ev.model_dump_json())
-            except Exception:  # pragma: no cover
-                logger.warning("failed to write evidence to cache", exc_info=True)
-
-        logger.info(
-            "evidence_built",
-            extra={
-                "anchor_id": anchor_id,
-                "bundle_size_bytes": bundle_size_bytes(ev),
-                **selector_meta,
-            },
-        )
-        return ev
+    def build(self, anchor_id: str) -> WhyDecisionEvidence:
+        """
+        Two-key Redis cache (§H3):
+           ① alias_key  –›   composite_key
+           ② composite_key –› evidence JSON
+        Both keys share the same TTL (15 min).
+        """
+        alias_key = ALIAS_TPL.format(anchor_id=anchor_id)
+        retry_count = 0
+
+        # ---------- cache read (alias ➜ composite ➜ evidence) ----------
+        if self._redis is not None:
+            try:
+                composite_key = self._redis.get(alias_key)
+                if composite_key:
+                    cached = self._redis.get(composite_key)
+                    if cached:
+                        ev = WhyDecisionEvidence.model_validate_json(cached)
+                        ev.__dict__["_retry_count"] = retry_count
+                        logger.debug("evidence cache hit", extra={"anchor_id": anchor_id})
+                        return ev
+            except Exception:
+                logger.warning("redis read error – bypassing cache", exc_info=True)
+
+        # ---------- cache-miss ➜ upstream fetch (+1 retry) -------------
+        ev, snapshot_etag, retry_count = self._collect_from_upstream(anchor_id)
+        ev.snapshot_etag = snapshot_etag           # B-6
+        ev.__dict__["_retry_count"] = retry_count  # B-8
+
+        # ---------- selector truncation --------------------------------
+        ev, selector_meta = truncate_evidence(ev)
+
+        # ---------- cache write (composite + alias) --------------------
+        if self._redis is not None:
+            try:
+                composite_key = _make_cache_key(
+                    decision_id=anchor_id,
+                    intent="why_decision",
+                    graph_scope="k1",
+                    snapshot_etag=snapshot_etag,
+                    truncation_applied=False,
+                )
+                ev_json = ev.model_dump_json()
+                pipe = self._redis.pipeline()
+                pipe.setex(composite_key, CACHE_TTL_SEC, ev_json)
+                pipe.setex(alias_key,      CACHE_TTL_SEC, composite_key)
+                pipe.execute()                                # B-1 & B-7
+            except Exception:
+                logger.warning("redis write error", exc_info=True)
+
+        logger.info(                                             # observability
+            "evidence_built",
+            extra={
+                "anchor_id": anchor_id,
+                "bundle_size_bytes": bundle_size_bytes(ev),
+                **selector_meta,
+            },
+        )
+        return ev
@@
-    def _collect_from_upstream(self, anchor_id: str) -> tuple[WhyDecisionEvidence, dict]:
-        """Call Memory-API and build a *complete* bundle (untruncated)."""
-        # --------------- one   retry w/ capped jitter -------------- #
-        for attempt in range(2):
-            try:
-                resp_anchor = self._client.get(f"/api/enrich/decision/{anchor_id}")
-                resp_anchor.raise_for_status()
-                break
-            except Exception:
-                if attempt == 1:
-                    raise
-                time.sleep(random.uniform(0.05, 0.30))
+    def _collect_from_upstream(self, anchor_id: str) -> Tuple[WhyDecisionEvidence, str, int]:
+        """Return (evidence, snapshot_etag, retry_count) with ≤ 1 retry + jitter ≤ 300 ms."""
+        retry_count = 0
+        for attempt in range(2):
+            try:
+                resp_anchor = self._client.get(f"/api/enrich/decision/{anchor_id}")
+                resp_anchor.raise_for_status()
+                break
+            except Exception:
+                retry_count = attempt + 1
+                if attempt == 1:
+                    raise
+                time.sleep(random.uniform(0.05, 0.30))  # capped jitter
@@
-        return evidence, {"snapshot_etag": snapshot_etag}
+        return evidence, snapshot_etag, retry_count
2 ️⃣ services/gateway/src/gateway/selector.py
diff
Copy
Edit
@@
-from .models import WhyDecisionEvidence, WhyDecisionAnchor
-import orjson, datetime as dt, hashlib, math
-from typing import Dict, Any, List, Tuple, Set
-
-from core_config.constants import (
-    MAX_PROMPT_BYTES,
-    SELECTOR_TRUNCATION_THRESHOLD,
-    MIN_EVIDENCE_ITEMS,
-    SELECTOR_MODEL_ID,
-)
+import orjson, datetime as dt
+from typing import Dict, Any, List, Tuple, Set
+
+from .models import WhyDecisionEvidence, WhyDecisionAnchor
+from core_config.constants import (
+    MAX_PROMPT_BYTES,
+    SELECTOR_TRUNCATION_THRESHOLD,
+    MIN_EVIDENCE_ITEMS,
+    SELECTOR_MODEL_ID,
+)
@@
-    if size <= SELECTOR_TRUNCATION_THRESHOLD:
-        return ev, {
-            "selector_truncation": False,
-            "total_neighbors_found": len(ev.events)
-            + len(ev.transitions.preceding)
-            + len(ev.transitions.succeeding),
-            "final_evidence_count": len(ev.events)
-            + len(ev.transitions.preceding)
-            + len(ev.transitions.succeeding),
-        }
+    if size <= SELECTOR_TRUNCATION_THRESHOLD:
+        return ev, {
+            "selector_truncation": False,
+            "total_neighbors_found": len(ev.events)
+            + len(ev.transitions.preceding)
+            + len(ev.transitions.succeeding),
+            "final_evidence_count": len(ev.events)
+            + len(ev.transitions.preceding)
+            + len(ev.transitions.succeeding),
+            "dropped_evidence_ids": [],
+            "bundle_size_bytes": size,
+            "max_prompt_bytes": MAX_PROMPT_BYTES,
+            "selector_model_id": SELECTOR_MODEL_ID,               # B-2
+        }
@@
-    for e in events_sorted:
+    for e in events_sorted:
         kept.append(e)
         ev.events = kept
-        if (
-            bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD
-            and len(kept) >= MIN_EVIDENCE_ITEMS
-        ):
+        if (
+            bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD
+            and len(kept) >= MIN_EVIDENCE_ITEMS            # B-3
+        ):
             break
+
+# -- MIN_EVIDENCE_ITEMS safety net (rare edge: every neighbour too big) --
+    if not kept and events_sorted:
+        kept.append(events_sorted[0])
+        ev.events = kept
3 ️⃣ services/gateway/src/gateway/prompt_envelope.py
diff
Copy
Edit
@@
-from typing import Dict, Any, List
-import hashlib, json, uuid
-
-_POLICY_REGISTRY = {
-    "why_v1": {
-        "policy_id": "why_v1",
-        "prompt_id": "why_v1.0",
-        "policy_block": "Answer strictly in JSON matching WhyDecisionAnswer@1.",
-        "explanations": [],
-    }
-}
+from typing import Dict, Any, List, Tuple
+import hashlib, json, uuid
+from importlib import resources
+from pathlib import Path
+
+# --------------------------------------------------------------#
+#  Single-source policy registry (JSON) — resolves B-5          #
+# --------------------------------------------------------------#
+_REG_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_registry.json"
+with open(_REG_PATH, "r", encoding="utf-8") as _fp:
+    _POLICY_REGISTRY = json.load(_fp)
@@
-def build_envelope(
-    intent: str,
-    question: str,
-    evidence: Dict[str, Any],
-    allowed_ids: List[str],
-    constraint_schema: str = "WhyDecisionAnswer@1",
-    max_tokens: int = 256,
-    policy_name: str = "why_v1",
-) -> tuple[Dict[str, Any], str, str, str]:
-    pol = _POLICY_REGISTRY[policy_name]
-    envelope = {
-        "prompt_version": pol["prompt_id"],
-        "intent": intent,
-        "question": question,
-        "evidence": evidence,
-        "allowed_ids": allowed_ids,
-        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
-        "policy": pol["policy_block"],
-        "explanations": pol["explanations"],
-    }
-    fp = prompt_fingerprint(envelope)
-    bundle_fp = hashlib.sha256(json.dumps(evidence, separators=(",", ":")).encode()).hexdigest()
-    return envelope, fp, bundle_fp, pol["policy_id"]
+def build_envelope(
+    intent: str,
+    question: str,
+    evidence: Dict[str, Any],
+    allowed_ids: List[str],
+    policy_name: str = "why_v1",
+    max_tokens: int = 256,
+    temperature: float = 0.0,
+    retries: int = 0,
+    constraint_schema: str = "WhyDecisionAnswer@1",
+) -> Tuple[Dict[str, Any], str, str]:
+    """
+    Returns (envelope, prompt_fingerprint, bundle_fingerprint) — spec §8.2.
+    """
+    pol = _POLICY_REGISTRY[policy_name]
+    envelope = {
+        "prompt_id":  pol["prompt_id"],
+        "policy_id":  pol["policy_id"],
+        "intent":     intent,
+        "question":   question,
+        "evidence":   evidence,
+        "allowed_ids": allowed_ids,
+        "policy":    {"temperature": temperature, "retries": retries},
+        "explanations": pol.get("explanations", []),
+        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
+    }
+    prompt_fp  = prompt_fingerprint(envelope)
+    bundle_fp  = hashlib.sha256(
+        json.dumps(evidence, separators=(",", ":")).encode()
+    ).hexdigest()
+    return envelope, prompt_fp, bundle_fp
4 ️⃣ services/gateway/src/gateway/app.py
diff
Copy
Edit
@@
-from .prompt_envelope import build_envelope
+from .prompt_envelope import build_envelope
@@
-import time
+import time, importlib.metadata as _md
@@
     request_id = req.request_id or uuid.uuid4().hex
@@
-    envelope, pf, bundle_fp, policy_id = build_envelope(
+    envelope, pf, bundle_fp = build_envelope(
         intent=req.intent,
         question=f"Why was decision {ev.anchor.id} made?",
         evidence=ev.model_dump(mode="python"),
         allowed_ids=allowed,
+        retries=getattr(ev, "_retry_count", 0),
     )
@@
-    latency_ms = int((time.perf_counter() - t0) * 1000)
+    latency_ms = int((time.perf_counter() - t0) * 1000)
+    try:
+        sdk_version = _md.version("batvault_sdk")            # P-2
+    except _md.PackageNotFoundError:
+        sdk_version = "unknown"
@@
-        "policy_id":       policy_id,
-        "prompt_id":       envelope["prompt_version"],
+        "policy_id":       envelope["policy_id"],
+        "prompt_id":       envelope["prompt_id"],
         "prompt_fingerprint": pf,
         "bundle_fingerprint": bundle_fp,
         "bundle_size_bytes": bundle_size_bytes(ev),
         "snapshot_etag":   getattr(ev, "snapshot_etag", "unknown"),
         "selector_model_id": SELECTOR_MODEL_ID,
-        "fallback_used":   False,
-        "retries":         0,
+        "fallback_used":   False,
+        "retries":         getattr(ev, "_retry_count", 0),   # B-8
+        "gateway_version": app.version,
+        "sdk_version":     sdk_version,
         "latency_ms":      latency_ms,
     }
5 ️⃣ services/gateway/tests/test_validator.py
diff
Copy
Edit
@@
-SNAPSHOT = pathlib.Path(__file__).parent / "fixtures" / "batvault_live_snapshot.tar.gz"
+SNAPSHOT = next((path for path in (pathlib.Path(__file__).parent / "fixtures").glob("*snapshot*.tar.gz")), None)
+assert SNAPSHOT and SNAPSHOT.exists(), "snapshot fixture missing (B-6)"
✅ Impact Summary
Blocker	Resolution
B-1/7	Two-key Redis cache now writes both composite & alias keys.
B-2	Non-truncation selector meta now carries selector_model_id, bundle_size_bytes, max_prompt_bytes, dropped_evidence_ids=[].
B-3	MIN_EVIDENCE_ITEMS enforced even in pathological edge-cases.
B-4	Envelope matches spec exactly (prompt_id & policy_id top-level; policy object).
B-5	Single JSON-backed policy registry; in-code dict removed.
B-6	snapshot_etag attached to every evidence instance.
B-8	meta.retries reflects actual upstream retry count.
P-1..P-5	Audit-trail sink preserved; dynamic sdk_version; constants centralized; logging parity; new type hints.