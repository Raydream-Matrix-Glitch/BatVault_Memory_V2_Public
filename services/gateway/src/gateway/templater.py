from typing import List, Tuple
import hashlib, re
from core_logging import get_logger
from core_models.models import WhyDecisionEvidence, WhyDecisionAnswer

logger = get_logger("templater")

def build_allowed_ids(ev: WhyDecisionEvidence) -> List[str]:
    ids = {ev.anchor.id}
    ids.update([e.get("id") for e in ev.events if isinstance(e, dict) and e.get("id")])
    ids.update([t.get("id") for t in ev.transitions.preceding if isinstance(t, dict) and t.get("id")])
    ids.update([t.get("id") for t in ev.transitions.succeeding if isinstance(t, dict) and t.get("id")])
    return sorted(x for x in ids if x)

_ALIAS_RE = re.compile(r"^[AET]\d+$")

def _pretty_anchor(node_id: str) -> str:
    """
    Display-only alias (spec M-3, 2025-07-20)
      • Real aliases like “A1”/“E3” stay unchanged
      • IDs ≤20 chars stay unchanged (fixtures)
      • Otherwise single-anchor bundles map deterministically to “A1”
    """
    if _ALIAS_RE.match(node_id) or len(node_id) <= 20:
        return node_id
    logger.debug("alias_mapped", extra={"node_id": node_id, "alias": "A1"})
    return "A1"

def _det_short_answer(anchor_id: str, events_n: int, preceding_n: int,
                      succeeding_n: int, supporting_n: int,
                      allowed_n: int) -> str:
    anchor_disp = _pretty_anchor(anchor_id)
    return (f"Decision {anchor_disp}: {events_n} event(s), "
            f"{preceding_n} preceding, {succeeding_n} succeeding. "
            f"Cited {supporting_n}/{allowed_n} evidence item(s).")[:320]

# Polymorphic wrapper so call‑sites can pass WhyDecisionEvidence or explicit args
def deterministic_short_answer(*args, **kwargs):  # type: ignore[override]
    if args and isinstance(args[0], WhyDecisionEvidence):
        ev = args[0]
        return _det_short_answer(ev.anchor.id if ev.anchor else "unknown",
                                 len(ev.events or []),
                                 len(getattr(ev.transitions, "preceding", []) or []),
                                 len(getattr(ev.transitions, "succeeding", []) or []),
                                 len(getattr(ev, "supporting_ids", []) or []),
                                 len(ev.allowed_ids or []))
    return _det_short_answer(*args, **kwargs)

def validate_and_fix(answer: WhyDecisionAnswer, allowed_ids: List[str], anchor_id: str
                    ) -> Tuple[WhyDecisionAnswer, bool, List[str]]:
    # treat compact alias (“A1”) as equivalent to long slug for IN-operator checks
    disp_anchor = _pretty_anchor(anchor_id)
    allowed = set(allowed_ids) | {disp_anchor}
    orig_support = list(answer.supporting_ids)
    support = [x for x in orig_support if x in allowed]
    changed = len(support) != len(orig_support)
    if disp_anchor not in support:          # anchor must be first (alias form)
        support = [disp_anchor] + [x for x in support if x != disp_anchor]
        changed = True
    errs: List[str] = []
    if changed:
        errs.append("supporting_ids adjusted to fit allowed_ids and include anchor")
    answer.supporting_ids = support
    return answer, changed, errs
