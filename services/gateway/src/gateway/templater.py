from typing import List, Tuple
from core_models.models import WhyDecisionEvidence, WhyDecisionAnswer

def build_allowed_ids(ev: WhyDecisionEvidence) -> List[str]:
    ids = {ev.anchor.id}
    ids.update([e.get("id") for e in ev.events if isinstance(e, dict) and e.get("id")])
    ids.update([t.get("id") for t in ev.transitions.preceding if isinstance(t, dict) and t.get("id")])
    ids.update([t.get("id") for t in ev.transitions.succeeding if isinstance(t, dict) and t.get("id")])
    return sorted(x for x in ids if x)

def _det_short_answer(anchor_id: str, events_n: int, preceding_n: int, succeeding_n: int,
                      supporting_n: int, allowed_n: int) -> str:
    return (f"Decision {anchor_id}: {events_n} event(s), {preceding_n} preceding, {succeeding_n} succeeding. "
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
    allowed = set(allowed_ids)
    orig_support = list(answer.supporting_ids)
    support = [x for x in orig_support if x in allowed]
    changed = len(support) != len(orig_support)
    if anchor_id not in support:
        support = [anchor_id] + [x for x in support if x != anchor_id]
        changed = True
    errs: List[str] = []
    if changed:
        errs.append("supporting_ids adjusted to fit allowed_ids and include anchor")
    answer.supporting_ids = support
    return answer, changed, errs
