0 ️⃣ Config helper — NEW
packages/core_config/src/core_config/constants.py

python
Copy
Edit
"""
Shared constants (single-source-of-truth across services).
"""
MAX_PROMPT_BYTES = 8192
SELECTOR_TRUNCATION_THRESHOLD = 6144
MIN_EVIDENCE_ITEMS = 1
SELECTOR_MODEL_ID = "deterministic_v0"  # spec §9.1
1 ️⃣ Evidence cache-key & retry — PATCH
services/gateway/src/gateway/evidence.py

diff
Copy
Edit
@@
-import httpx, orjson, redis
+import httpx, orjson, redis, hashlib, random, time

@@
-CACHE_TTL_SEC = 900  # 15 min – spec §9.3
+CACHE_TTL_SEC = 900  # 15 min – spec §9.3

+# ------------------------------------------------------------------ #
+#  helpers                                                          #
+# ------------------------------------------------------------------ #
+def _make_cache_key(
+    decision_id: str,
+    intent: str,
+    graph_scope: str,
+    snapshot_etag: str,
+    truncation_applied: bool,
+) -> str:
+    parts = (decision_id, intent, graph_scope, snapshot_etag, str(truncation_applied))
+    return "evidence:" + hashlib.sha256("|".join(parts).encode()).hexdigest()

 class EvidenceBuilder:
@@
-        cache_key = f"evidence:{anchor_id}"
+        # ---------------- composite cache key ---------------------- #
+        cache_key = _make_cache_key(
+            decision_id=anchor_id,
+            intent="why_decision",
+            graph_scope="k1",
+            snapshot_etag=ev_headers.get("snapshot_etag", "unknown"),
+            truncation_applied=False,  # we cache the *full* bundle
+        )
@@
-        ev = self._collect_from_upstream(anchor_id)
+        ev, ev_headers = self._collect_from_upstream(anchor_id)
@@
-            self._redis.setex(cache_key, CACHE_TTL_SEC, ev.model_dump_json())
+            self._redis.setex(cache_key, CACHE_TTL_SEC, ev.model_dump_json())
@@
-    def _collect_from_upstream(self, anchor_id: str) -> WhyDecisionEvidence:
+    def _collect_from_upstream(self, anchor_id: str) -> tuple[WhyDecisionEvidence, dict]:
@@
-        try:
-            anchor_data = self._client.get(f"/api/enrich/decision/{anchor_id}").json()
+        # --------------- one   retry w/ capped jitter -------------- #
+        for attempt in range(2):
+            try:
+                resp_anchor = self._client.get(f"/api/enrich/decision/{anchor_id}")
+                resp_anchor.raise_for_status()
+                break
+            except Exception:
+                if attempt == 1:
+                    raise
+                time.sleep(random.uniform(0.05, 0.30))
+
+        anchor_data = resp_anchor.json()
+        snapshot_etag = resp_anchor.headers.get("snapshot_etag", "unknown")
@@
-        return evidence
+        return evidence, {"snapshot_etag": snapshot_etag}
2 ️⃣ Selector baseline & quotas — PATCH
services/gateway/src/gateway/selector.py

diff
Copy
Edit
-import orjson, datetime as dt
-from typing import Dict, Any, List, Tuple
+import orjson, datetime as dt, hashlib, math
+from typing import Dict, Any, List, Tuple, Set
-from .models import WhyDecisionEvidence
-from .models import WhyDecisionEvidence, WhyDecisionAnchor
+from .models import WhyDecisionEvidence, WhyDecisionAnchor
+from core_config.constants import (
+    MAX_PROMPT_BYTES,
+    SELECTOR_TRUNCATION_THRESHOLD,
+    MIN_EVIDENCE_ITEMS,
+    SELECTOR_MODEL_ID,
+)

-def _score(item: Dict[str, Any]) -> Tuple[int, float]:
-    # Recency (newer → larger UNIX‐ts); similarity placeholder = 0.0
+def _text_tokens(s: str | None) -> Set[str]:
+    return set((s or "").lower().split())
+
+def _sim(a: str | None, b: str | None) -> float:
+    """Jaccard similarity between two short texts (deterministic baseline)."""
+    ta, tb = _text_tokens(a), _text_tokens(b)
+    inter = ta & tb
+    union = ta | tb
+    return 0.0 if not union else len(inter) / len(union)
+
+def _score(item: Dict[str, Any], anchor: WhyDecisionAnchor) -> Tuple[int, float]:
+    # Recency + similarity (baseline)
     ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
-    return (int(ts.timestamp()), 0.0)
+    sim = _sim(item.get("summary") or item.get("description"), anchor.rationale)
+    return (int(ts.timestamp()), sim)

@@
-    events_sorted = sorted(ev.events, key=_score, reverse=True)
+    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor), reverse=True)
@@
-    for e in events_sorted:
+    for e in events_sorted:
         kept.append(e)
         ev.events = kept
-        if bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD:
+        if (
+            bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD
+            and len(kept) >= MIN_EVIDENCE_ITEMS
+        ):
             break
@@
-    meta = {
+    meta = {
         "selector_truncation": True,
         "total_neighbors_found": len(events_sorted),
@@
         "bundle_size_bytes": bundle_size_bytes(ev),
         "max_prompt_bytes": MAX_PROMPT_BYTES,
+        "selector_model_id": SELECTOR_MODEL_ID,
     }
     return ev, meta
3 ️⃣ Prompt envelope & artifact retention — PATCH
services/gateway/src/gateway/prompt_envelope.py

diff
Copy
Edit
-from typing import Dict, Any, List
+from typing import Dict, Any, List
+import hashlib, json, uuid
+
+_POLICY_REGISTRY = {
+    "why_v1": {
+        "policy_id": "why_v1",
+        "prompt_id": "why_v1.0",
+        "policy_block": "Answer strictly in JSON matching WhyDecisionAnswer@1.",
+        "explanations": [],
+    }
+}

@@
-def build_envelope(
-    intent: str,
-    question: str,
-    evidence: Dict[str, Any],
-    allowed_ids: List[str],
-    constraint_schema: str = "WhyDecisionAnswer@1",
-    max_tokens: int = 256,
-    prompt_version: str = "why_v1",
-) -> tuple[Dict[str, Any], str]:
-    envelope = {
-        "prompt_version": prompt_version,
-        "intent": intent,
-        "question": question,
-        "evidence": evidence,
-        "allowed_ids": allowed_ids,
-        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
-    }
-    return envelope, prompt_fingerprint(envelope)
+def build_envelope(
+    intent: str,
+    question: str,
+    evidence: Dict[str, Any],
+    allowed_ids: List[str],
+    constraint_schema: str = "WhyDecisionAnswer@1",
+    max_tokens: int = 256,
+    policy_name: str = "why_v1",
+) -> tuple[Dict[str, Any], str, str, str]:
+    pol = _POLICY_REGISTRY[policy_name]
+    envelope = {
+        "prompt_version": pol["prompt_id"],
+        "intent": intent,
+        "question": question,
+        "evidence": evidence,
+        "allowed_ids": allowed_ids,
+        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
+        "policy": pol["policy_block"],
+        "explanations": pol["explanations"],
+    }
+    fp = prompt_fingerprint(envelope)
+    bundle_fp = hashlib.sha256(json.dumps(evidence, separators=(",", ":")).encode()).hexdigest()
+    return envelope, fp, bundle_fp, pol["policy_id"]
4 ️⃣ Gateway orchestration & MinIO — PATCH
services/gateway/src/gateway/app.py

diff
Copy
Edit
@@
-from .prompt_envelope import build_envelope
+from .prompt_envelope import build_envelope
+from core_storage.minio import MinioClient
+from core_config.constants import SELECTOR_MODEL_ID
+minio = MinioClient.from_env()
@@
-    envelope, fp = build_envelope(
+    envelope, fp, bundle_fp, policy_id = build_envelope(
         intent=req.intent,
         question=f"Why was decision {ev.anchor.id} made?",
         evidence=ev.model_dump(mode="python"),
         allowed_ids=allowed,
     )
+
+    # ----- persist envelope for audit trail (spec §8.3) ----------
+    minio.put_json(f"{request_id}/envelope.json", envelope)
@@
-    meta = {
-        "prompt_fingerprint": fp,
+    meta = {
+        "policy_id": policy_id,
+        "prompt_id": envelope["prompt_version"],
+        "prompt_fingerprint": fp,
+        "bundle_fingerprint": bundle_fp,
         "bundle_size_bytes": bundle_size_bytes(ev),
         "fallback_used": False,
         "latency_ms": int((time.perf_counter() - t0) * 1000),
+        "snapshot_etag": getattr(ev, "snapshot_etag", "unknown"),
     }
@@
-    valid, errors = validate_response(resp)
+    valid, errors = validate_response(resp)
@@
 logger.info(
     "evidence_built",
     extra={
         "anchor_id": anchor_id,
         "bundle_size_bytes": bundle_size_bytes(ev),
+        "selector_model_id": SELECTOR_MODEL_ID,
         **selector_meta,
     },
 )
5 ️⃣ Validator schema check — PATCH
packages/core_validator/src/core_validator/validator.py

diff
Copy
Edit
-from gateway.models import WhyDecisionResponse
+from gateway.models import WhyDecisionResponse
+from gateway.models import WhyDecisionAnswer  # generated Pydantic model
@@
-    allowed = set(resp.evidence.allowed_ids)
+    # -------- schema validation ---------------------------------- #
+    try:
+        WhyDecisionAnswer.model_validate(resp.answer.model_dump(mode="python"))
+    except Exception as exc:
+        errs.append(f"answer schema error: {exc}")
+
+    allowed = set(resp.evidence.allowed_ids)
@@
     return (not errs), errs
6 ️⃣ Policy registry stub — NEW
services/gateway/config/policy_registry.json

json
Copy
Edit
{
  "why_v1": {
    "policy_id": "why_v1",
    "prompt_id": "why_v1.0",
    "policy_block": "Answer strictly in JSON matching WhyDecisionAnswer@1.",
    "explanations": []
  }
}
7 ️⃣ Updated unit tests — PATCH
services/gateway/tests/test_validator.py

diff
Copy
Edit
@@
-def test_validator_subset_rule():
+import json, tarfile, pathlib
+
+SNAPSHOT = pathlib.Path(__file__).parent / "fixtures" / "batvault_live_snapshot.tar.gz"
+
+def _anchor_id_from_snapshot() -> str:
+    with tarfile.open(SNAPSHOT) as t:
+        first_decision = next(f for f in t.getnames() if f.endswith(".json") and "/decisions/" in f)
+        return pathlib.Path(first_decision).stem
+
+
+def test_validator_subset_rule():
-    ev = WhyDecisionEvidence(
-        anchor=WhyDecisionAnchor(id="A1"),
+    anchor_id = _anchor_id_from_snapshot()
+    ev = WhyDecisionEvidence(
+        anchor=WhyDecisionAnchor(id=anchor_id),
         events=[],
         transitions=WhyDecisionTransitions(),
-        allowed_ids=["A1", "E1"],
+        allowed_ids=[anchor_id, "E1"],
     )
@@
     assert not errs
+
+def test_validator_missing_anchor():
+    ev = WhyDecisionEvidence(
+        anchor=WhyDecisionAnchor(id="D-X"),
+        events=[],
+        transitions=WhyDecisionTransitions(),
+        allowed_ids=["E1"],
+    )
+    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=["E1"])
+    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
+                               completeness_flags=CompletenessFlags(), meta={})
+    ok, errs = validate_response(resp)
+    assert not ok
+    assert "anchor.id missing" in errs[0]