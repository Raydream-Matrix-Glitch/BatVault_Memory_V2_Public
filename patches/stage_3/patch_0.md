1 ️⃣ services/gateway/src/gateway/evidence.py — NEW
python
Copy
Edit
"""
Evidence Builder for Milestone 3.

- Pulls anchor + k=1 neighbours from Memory-API
- Caches bundles in Redis for 15 min (invalidated automatically when TTL expires)
- Delegates deterministic truncation to Selector when bundle > threshold
"""
from __future__ import annotations

import httpx, orjson, redis
from typing import List, Dict, Any, Optional
from pydantic import ValidationError

from core_logging import get_logger
from core_config import get_settings
from .models import WhyDecisionEvidence, WhyDecisionAnchor, WhyDecisionTransitions
from .selector import truncate_evidence, bundle_size_bytes

CACHE_TTL_SEC = 900  # 15 min – spec §9.3
logger = get_logger("evidence_builder")
settings = get_settings()


class EvidenceBuilder:
    """Collect & return a **validated** evidence bundle for *anchor_id*."""

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=3.0, base_url=settings.memory_api_url)
        try:
            self._redis: Optional[redis.Redis] = redis.Redis.from_url(settings.redis_url)
        except Exception:  # pragma: no cover
            self._redis = None

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #
    def build(self, anchor_id: str) -> WhyDecisionEvidence:
        cache_key = f"evidence:{anchor_id}"
        if self._redis is not None:
            cached = self._redis.get(cache_key)
            if cached:
                try:
                    ev = WhyDecisionEvidence.model_validate_json(cached)
                    logger.debug("evidence cache hit", extra={"anchor_id": anchor_id})
                    return ev
                except ValidationError:
                    logger.warning("cached evidence failed validation; purging", extra={"anchor_id": anchor_id})
                    self._redis.delete(cache_key)

        ev = self._collect_from_upstream(anchor_id)
        ev, selector_meta = truncate_evidence(ev)

        if self._redis is not None:
            try:
                self._redis.setex(cache_key, CACHE_TTL_SEC, ev.model_dump_json())
            except Exception:  # pragma: no cover
                logger.warning("failed to write evidence to cache", exc_info=True)

        logger.info(
            "evidence_built",
            extra={
                "anchor_id": anchor_id,
                "bundle_size_bytes": bundle_size_bytes(ev),
                **selector_meta,
            },
        )
        return ev

    # ------------------------------------------------------------------ #
    #  Helpers                                                           #
    # ------------------------------------------------------------------ #
    def _collect_from_upstream(self, anchor_id: str) -> WhyDecisionEvidence:
        """Call Memory-API and build a *complete* bundle (untruncated)."""
        try:
            anchor_data = self._client.get(f"/api/enrich/decision/{anchor_id}").json()
            anchor = WhyDecisionAnchor(**anchor_data)

            neigh = self._client.post(
                "/api/graph/expand_candidates", json={"id": anchor_id, "k": 1}
            ).json()

            events = neigh.get("events", [])
            trans_pre = neigh.get("preceding", [])
            trans_suc = neigh.get("succeeding", [])

        except Exception:  # pragma: no cover
            logger.error("memory_api_error", exc_info=True, extra={"anchor_id": anchor_id})
            anchor = WhyDecisionAnchor(id=anchor_id)
            events, trans_pre, trans_suc = [], [], []

        evidence = WhyDecisionEvidence(
            anchor=anchor,
            events=events,
            transitions=WhyDecisionTransitions(preceding=trans_pre, succeeding=trans_suc),
        )

        ids = {anchor.id}
        ids.update([e.get("id") for e in events if isinstance(e, dict)])
        ids.update([t.get("id") for t in trans_pre if isinstance(t, dict)])
        ids.update([t.get("id") for t in trans_suc if isinstance(t, dict)])
        evidence.allowed_ids = sorted(i for i in ids if i)

        return evidence
2 ️⃣ services/gateway/src/gateway/selector.py — NEW
python
Copy
Edit
"""
Deterministic Selector – Milestone 3

Recency-first truncation when bundle > SELECTOR_TRUNCATION_THRESHOLD bytes.
"""
from __future__ import annotations
import orjson, datetime as dt
from typing import Dict, Any, List, Tuple

from .models import WhyDecisionEvidence

MAX_PROMPT_BYTES = 8192
SELECTOR_TRUNCATION_THRESHOLD = 6144


def bundle_size_bytes(ev: WhyDecisionEvidence) -> int:
    return len(orjson.dumps(ev.model_dump(mode="python")))


def _parse_ts(item: Dict[str, Any]) -> dt.datetime | None:
    ts = item.get("timestamp")
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _score(item: Dict[str, Any]) -> Tuple[int, float]:
    # Recency (newer → larger UNIX‐ts); similarity placeholder = 0
    ts = _parse_ts(item) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (int(ts.timestamp()), 0.0)


def truncate_evidence(ev: WhyDecisionEvidence) -> Tuple[WhyDecisionEvidence, Dict[str, Any]]:
    """Return (possibly) truncated evidence and selector_meta."""
    size = bundle_size_bytes(ev)
    if size <= SELECTOR_TRUNCATION_THRESHOLD:
        return ev, {
            "selector_truncation": False,
            "total_neighbors_found": len(ev.events)
            + len(ev.transitions.preceding)
            + len(ev.transitions.succeeding),
            "final_evidence_count": len(ev.events)
            + len(ev.transitions.preceding)
            + len(ev.transitions.succeeding),
        }

    events_sorted = sorted(ev.events, key=_score, reverse=True)
    kept: List[Dict[str, Any]] = []
    for e in events_sorted:
        kept.append(e)
        ev.events = kept
        if bundle_size_bytes(ev) <= SELECTOR_TRUNCATION_THRESHOLD:
            break

    ids = {ev.anchor.id}
    ids.update([e.get("id") for e in kept if isinstance(e, dict)])
    ids.update([t.get("id") for t in ev.transitions.preceding if t.get("id")])
    ids.update([t.get("id") for t in ev.transitions.succeeding if t.get("id")])
    ev.allowed_ids = sorted(i for i in ids if i)

    meta = {
        "selector_truncation": True,
        "total_neighbors_found": len(events_sorted),
        "final_evidence_count": len(kept)
        + len(ev.transitions.preceding)
        + len(ev.transitions.succeeding),
        "dropped_evidence_ids": [x.get("id") for x in events_sorted[len(kept) :] if x.get("id")],
        "bundle_size_bytes": bundle_size_bytes(ev),
        "max_prompt_bytes": MAX_PROMPT_BYTES,
    }
    return ev, meta
3 ️⃣ services/gateway/src/gateway/prompt_envelope.py — NEW
python
Copy
Edit
"""
Prompt Envelope Builder – Milestone 3
"""
from __future__ import annotations
from typing import Dict, Any, List
from core_utils.fingerprints import prompt_fingerprint


def build_envelope(
    intent: str,
    question: str,
    evidence: Dict[str, Any],
    allowed_ids: List[str],
    constraint_schema: str = "WhyDecisionAnswer@1",
    max_tokens: int = 256,
    prompt_version: str = "why_v1",
) -> tuple[Dict[str, Any], str]:
    envelope = {
        "prompt_version": prompt_version,
        "intent": intent,
        "question": question,
        "evidence": evidence,
        "allowed_ids": allowed_ids,
        "constraints": {"output_schema": constraint_schema, "max_tokens": max_tokens},
    }
    return envelope, prompt_fingerprint(envelope)
4 ️⃣ packages/core_validator/src/core_validator/validator.py — NEW
python
Copy
Edit
"""
Core Validator – Milestone 3
"""
from __future__ import annotations
from typing import List, Tuple
from gateway.models import WhyDecisionResponse

__all__ = ["validate_response"]


def validate_response(resp: WhyDecisionResponse) -> Tuple[bool, List[str]]:
    errs: List[str] = []

    allowed = set(resp.evidence.allowed_ids)
    support = set(resp.answer.supporting_ids)

    if not support.issubset(allowed):
        errs.append("supporting_ids ⊈ allowed_ids")

    anchor_id = resp.evidence.anchor.id
    if anchor_id and anchor_id not in support:
        errs.append("anchor.id missing from supporting_ids")

    trans_ids = [
        t.get("id")
        for t in resp.evidence.transitions.preceding + resp.evidence.transitions.succeeding
        if t.get("id")
    ]
    if trans_ids and not set(trans_ids).issubset(support):
        errs.append("transition ids must be cited in supporting_ids")

    return (not errs), errs
Add simple re-export in packages/core_validator/src/core_validator/__init__.py:

python
Copy
Edit
"""Core-Validator package"""
from .validator import validate_response  # noqa: F401
5 ️⃣ services/gateway/src/gateway/app.py — PATCH
diff
Copy
Edit
--- a/services/gateway/src/gateway/app.py
+++ b/services/gateway/src/gateway/app.py
@@
-from .templater import build_allowed_ids, deterministic_short_answer, validate_and_fix
+# Milestone 3 additions
+from .evidence import EvidenceBuilder
+from .selector import bundle_size_bytes
+from .prompt_envelope import build_envelope
+from .templater import build_allowed_ids, deterministic_short_answer, validate_and_fix
+from core_validator import validate_response
@@
-settings = get_settings()
-logger = get_logger("gateway")
+# Initialise milestone 3 helpers
+settings = get_settings()
+logger = get_logger("gateway")
+_evidence_builder = EvidenceBuilder()
@@
-    ev = req.evidence
+    # ------------------------------------------------ evidence ----------- #
+    if req.evidence is None and req.anchor_id:
+        ev = _evidence_builder.build(req.anchor_id)
+    else:
+        ev = req.evidence or WhyDecisionEvidence(anchor=WhyDecisionAnchor(id=req.anchor_id))
@@
-    ans = req.answer or WhyDecisionAnswer(short_answer=short, supporting_ids=supporting)
+    ans = req.answer or WhyDecisionAnswer(short_answer=short, supporting_ids=supporting)
@@
-    meta = {"fallback_used": False, "latency_ms": int((time.perf_counter()-t0)*1000)}
+    envelope, fp = build_envelope(
+        intent=req.intent,
+        question=f"Why was decision {ev.anchor.id} made?",
+        evidence=ev.model_dump(mode="python"),
+        allowed_ids=allowed,
+    )
+
+    meta = {
+        "prompt_fingerprint": fp,
+        "bundle_size_bytes": bundle_size_bytes(ev),
+        "fallback_used": False,
+        "latency_ms": int((time.perf_counter() - t0) * 1000),
+    }
@@
-    resp = WhyDecisionResponse(intent=req.intent, evidence=ev, answer=ans,
-                               completeness_flags=flags, meta=meta)
+    resp = WhyDecisionResponse(
+        intent=req.intent,
+        evidence=ev,
+        answer=ans,
+        completeness_flags=flags,
+        meta=meta,
+    )
+
+    # ---------------------------- validation --------------------------- #
+    valid, errors = validate_response(resp)
+    if not valid:
+        logger.warning("validator_errors", extra={"errors": errors})
+        ans, _, _ = validate_and_fix(ans, allowed, ev.anchor.id)
+        resp.answer = ans
+        resp.meta["fallback_used"] = True
+        resp.meta["validator_errors"] = errors
@@
-    return resp
+    return resp
6 ️⃣ services/gateway/tests/test_validator.py — NEW (sanity check)
python
Copy
Edit
from gateway.models import WhyDecisionResponse, WhyDecisionAnswer, WhyDecisionAnchor, WhyDecisionEvidence, WhyDecisionTransitions, CompletenessFlags
from core_validator import validate_response

def test_validator_subset_rule():
    ev = WhyDecisionEvidence(
        anchor=WhyDecisionAnchor(id="A1"),
        events=[],
        transitions=WhyDecisionTransitions(),
        allowed_ids=["A1", "E1"],
    )
    ans = WhyDecisionAnswer(short_answer="x", supporting_ids=["A1"])
    resp = WhyDecisionResponse(intent="why_decision", evidence=ev, answer=ans,
                               completeness_flags=CompletenessFlags(), meta={})
    ok, errs = validate_response(resp)
    assert ok
    assert not errs