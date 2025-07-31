--- a/services/gateway/src/gateway/selector.py
+++ b/services/gateway/src/gateway/selector.py
@@
-from __future__ import annotations
-import orjson, datetime as dt, hashlib, math
-from typing import Dict, Any, List, Tuple, Set
-
-import orjson, datetime as dt
-from typing import Dict, Any, List, Tuple, Set
+from __future__ import annotations
+import datetime as dt
+import hashlib
+import math
+from typing import Any, Dict, List, Set, Tuple
+
+import orjson
 
 from .models import WhyDecisionEvidence, WhyDecisionAnchor
 from core_config.constants import (
     MAX_PROMPT_BYTES,
-    SELECTOR_TRUNCATION_THRESHOLD,
     MIN_EVIDENCE_ITEMS,
     SELECTOR_MODEL_ID,
 )
 
-MAX_PROMPT_BYTES = 8192
-SELECTOR_TRUNCATION_THRESHOLD = 6144
-
@@
-def _sim(a: str | None, b: str | None) -> float:
-    """Jaccard similarity between two short texts (deterministic baseline)."""
-    ta, tb = _text_tokens(a), _text_tokens(b)
-    inter = ta & tb
-    union = ta | tb
-    sim = _sim(item.get("summary") or item.get("description"), anchor.rationale)
-    return (int(ts.timestamp()), sim)
+def _sim(a: str | None, b: str | None) -> float:
+    """Simple Jaccard similarity between two short texts."""
+    ta, tb = _text_tokens(a), _text_tokens(b)
+    if not ta or not tb:
+        return 0.0
+    return len(ta & tb) / len(ta | tb)
 
-def _score(item: Dict[str, Any], anchor: WhyDecisionAnchor) -> Tuple[int, float]:
-    # Recency + similarity (baseline)
-    ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
-    return (int(ts.timestamp()), 0.0)
+def _score(item: Dict[str, Any], anchor: WhyDecisionAnchor) -> Tuple[int, float]:
+    """Return (unix_ts, similarity) so that newer + more-similar items rank first."""
+    ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
+    sim = _sim(item.get("summary") or item.get("description"), anchor.rationale)
+    return (int(ts.timestamp()), sim)
 
@@
-    size = bundle_size_bytes(ev)
-    if size <= SELECTOR_TRUNCATION_THRESHOLD:
+    size = bundle_size_bytes(ev)
+    if size <= MAX_PROMPT_BYTES:
@@
-    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor), reverse=True)
+    events_sorted = sorted(ev.events, key=lambda it: _score(it, ev.anchor), reverse=True)
@@
-        if (
-            bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD
-            and len(kept) >= MIN_EVIDENCE_ITEMS            # B-3
+        if (
+            bundle_size_bytes(ev) <= MAX_PROMPT_BYTES
+            and len(kept) >= MIN_EVIDENCE_ITEMS            # B-3
             ):
diff
Copy
Edit
--- a/services/gateway/src/gateway/prompt_envelope.py
+++ b/services/gateway/src/gateway/prompt_envelope.py
@@
-from importlib import resources
-from pathlib import Path
-
-# --------------------------------------------------------------#
-#  Single-source policy registry (JSON) — resolves B-5          #
-# --------------------------------------------------------------#
-_REG_PATH = Path(__file__).resolve().parent.parent / "config" / "policy_registry.json"
-with open(_REG_PATH, "r", encoding="utf-8") as _fp:
-    _POLICY_REGISTRY = json.load(_fp)
+from pathlib import Path
+
+# --------------------------------------------------------------#
+#  Lazy-loaded policy registry to avoid FS dependency at import #
+# --------------------------------------------------------------#
+_POLICY_REGISTRY: Dict[str, Any] | None = None
+
+
+def _load_policy_registry() -> Dict[str, Any]:
+    global _POLICY_REGISTRY
+    if _POLICY_REGISTRY is None:
+        reg_path = Path(__file__).resolve().parent.parent / "config" / "policy_registry.json"
+        with open(reg_path, "r", encoding="utf-8") as fp:
+            _POLICY_REGISTRY = json.load(fp)
+    return _POLICY_REGISTRY
@@
-    pol = _POLICY_REGISTRY[policy_name]
+    pol = _load_policy_registry()[policy_name]
@@
-        "explanations": pol.get("explanations", []),
+        "explanations": pol.get("explanations", {}),
diff
Copy
Edit
--- a/packages/core_validator/src/core_validator/validator.py
+++ b/packages/core_validator/src/core_validator/validator.py
@@
-from typing import List, Tuple
-from gateway.models import WhyDecisionResponse
-from gateway.models import WhyDecisionAnswer  # generated Pydantic model
+from typing import List, Tuple
+from gateway.models import WhyDecisionResponse
 
 __all__ = ["validate_response"]
 
@@
-def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
-    errs: List[str] = []
-
-    # -------- schema validation ---------------------------------- #
-    try:
-        WhyDecisionAnswer.model_validate(resp.answer.model_dump(mode="python"))
-    except Exception as exc:
-        errs.append(f"answer schema error: {exc}")
-
-    # prompt_id / policy_id must be present (patch 5)
-    if not resp.meta.get("prompt_id"):
-        errs.append("meta.prompt_id missing")
-    if not resp.meta.get("policy_id"):
-        errs.append("meta.policy_id missing")
-
-    allowed = set(resp.evidence.allowed_ids)
-    support = set(resp.answer.supporting_ids)
-
-    if not support.issubset(allowed):
-        errs.append("supporting_ids ⊈ allowed_ids")
-
-    anchor_id = resp.evidence.anchor.id
-    if anchor_id and anchor_id not in support:
-        errs.append("anchor.id missing from supporting_ids")
-
-    trans_ids = [
-        t.get("id")
-        for t in resp.evidence.transitions.preceding + resp.evidence.transitions.succeeding
-        if t.get("id")
-    ]
-    if trans_ids and not set(trans_ids).issubset(support):
-        errs.append("transition ids m
-
-    return (not errs), errs
+def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
+    """Validate full WhyDecisionResponse and return (is_valid, errors)."""
+    errs: List[str] = []
+
+    # -------- schema validation ---------------------------------- #
+    try:
+        WhyDecisionResponse.model_validate(resp.model_dump(mode="python"))
+    except Exception as exc:
+        errs.append(f"response schema error: {exc}")
+
+    # Required meta fields
+    for key in ("prompt_id", "policy_id", "prompt_fingerprint", "bundle_fingerprint"):
+        if not resp.meta.get(key):
+            errs.append(f"meta.{key} missing")
+
+    # supporting_ids ⊆ allowed_ids
+    allowed = set(resp.evidence.allowed_ids)
+    support = set(resp.answer.supporting_ids)
+    if not support.issubset(allowed):
+        errs.append("supporting_ids ⊈ allowed_ids")
+
+    # anchor cited
+    anchor_id = resp.evidence.anchor.id
+    if anchor_id and anchor_id not in support:
+        errs.append("anchor.id missing from supporting_ids")
+
+    # transitions cited
+    trans_ids = [
+        t.get("id")
+        for t in resp.evidence.transitions.preceding + resp.evidence.transitions.succeeding
+        if t.get("id")
+    ]
+    if trans_ids and not set(trans_ids).issubset(support):
+        errs.append("transition ids must be cited in supporting_ids")
+
+    # completeness flags
+    cf = resp.completeness_flags
+    if cf.event_count != len(resp.evidence.events):
+        errs.append("completeness_flags.event_count mismatch")
+    if cf.has_preceding != bool(resp.evidence.transitions.preceding):
+        errs.append("completeness_flags.has_preceding mismatch")
+    if cf.has_succeeding != bool(resp.evidence.transitions.succeeding):
+        errs.append("completeness_flags.has_succeeding mismatch")
+
+    return (not errs), errs
diff
Copy
Edit
--- a/services/gateway/src/gateway/evidence.py
+++ b/services/gateway/src/gateway/evidence.py
@@
-        self._client = httpx.Client(timeout=3.0, base_url=settings.memory_api_url)
+        self._client = httpx.Client(timeout=httpx.Timeout(0.25), base_url=settings.memory_api_url)
diff
Copy
Edit
--- a/services/gateway/src/gateway/app.py
+++ b/services/gateway/src/gateway/app.py
@@
-    )
-
-    return resp
+    )
+
+    # -------- persist selector meta & validator report ----------- #
+    try:
+        client = minio_client()
+        selector_meta_blob = orjson.dumps(selector_meta)
+        client.put_object(
+            settings.minio_bucket,
+            f"{request_id}/selector_meta.json",
+            io.BytesIO(selector_meta_blob),
+            length=len(selector_meta_blob),
+            content_type="application/json",
+        )
+        if not valid and errors:
+            errors_blob = orjson.dumps(errors)
+            client.put_object(
+                settings.minio_bucket,
+                f"{request_id}/validator_errors.json",
+                io.BytesIO(errors_blob),
+                length=len(errors_blob),
+                content_type="application/json",
+            )
+    except Exception as exc:
+        logger.warning("minio_put_meta_failed", extra={"error": str(exc)})
+
+    return resp