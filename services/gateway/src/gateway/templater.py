from typing import List, Tuple
import re
from core_logging import get_logger
from core_models.models import WhyDecisionEvidence, WhyDecisionAnswer

logger = get_logger("templater")

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

def _det_short_answer(
    anchor_id: str,
    events_n: int,
    preceding_n: int,
    succeeding_n: int,
    supporting_n: int,
    allowed_n: int,
) -> str:
    """Generate a counts-based deterministic short answer.

    This variant is used when callers pass explicit numeric arguments and
    remains unchanged to preserve the existing templater contract used by
    golden tests.  The answer is truncated to 320 characters.
    """
    anchor_disp = _pretty_anchor(anchor_id)
    return (
        f"Decision {anchor_disp}: {events_n} event(s), "
        f"{preceding_n} preceding, {succeeding_n} succeeding. "
        f"Cited {supporting_n}/{allowed_n} evidence item(s)."
    )[:320]

def deterministic_short_answer(*args, **kwargs):  # type: ignore[override]
    """Polymorphic deterministic short answer.

    When passed a WhyDecisionEvidence instance as the first argument, this
    helper computes a counts-based summary derived from the evidence.
    When passed explicit numeric arguments (anchor_id, events_n, …), it
    defers to the counts-based version.  Truncation to 320 characters
    is applied uniformly.
    """
    if args and isinstance(args[0], WhyDecisionEvidence):
        ev: WhyDecisionEvidence = args[0]
        return _det_short_answer(
            ev.anchor.id if ev.anchor else "unknown",
            len(ev.events or []),
            len(getattr(ev.transitions, "preceding", []) or []),
            len(getattr(ev.transitions, "succeeding", []) or []),
            len(getattr(ev, "supporting_ids", []) or []),
            len(ev.allowed_ids or []),
        )
    return _det_short_answer(*args, **kwargs)

def validate_and_fix(
    answer: WhyDecisionAnswer, allowed_ids: List[str], anchor_id: str
) -> Tuple[WhyDecisionAnswer, bool, List[str]]:
    """Ensure supporting_ids are a subset of allowed_ids and include the anchor.

    If any supporting IDs are not in the allowed list they are removed; if
    the anchor ID is missing it is inserted at the front.  A boolean
    indicates whether changes were made and a list of string messages
    describes the adjustments.  This helper does not perform the full
    contract enforcement; it merely applies legacy fixes.  The core
    validator now owns the canonical enforcement logic.
    """
    allowed = set(allowed_ids)
    orig_support = list(answer.supporting_ids or [])
    support = [x for x in orig_support if x in allowed]
    changed = len(support) != len(orig_support)
    if anchor_id not in support:
        support = [anchor_id] + [x for x in support if x != anchor_id]
        changed = True
    errs: List[str] = []
    if changed:
        errs.append(
            "supporting_ids adjusted to fit allowed_ids and include anchor"
        )
    answer.supporting_ids = support
    return answer, changed, errs

def _compose_fallback_answer(ev: WhyDecisionEvidence) -> str:
    """Compose a deterministic, human-readable fallback answer.

    Two-step template:
      1) Lead: "<Maker> on <YYYY-MM-DD>: <rationale>." (or "<rationale>." if maker/date missing).
      2) If a succeeding transition exists, append " Next: <to_title>."
         Prefer `to_title`; fall back to a human-looking `to` string only if it is not an ID.
    Clamp to ≤320 chars and ≤2 sentences. Never emit raw IDs in the prose.
    """
    # Extract maker, date and rationale from the anchor if available.  When a
    # rationale ends with terminal punctuation (period, semicolon, colon or
    # comma) we strip it off so that the composed lead sentence never
    # contains a double punctuation mark.  This trimming applies only to
    # the trailing character and preserves internal punctuation.
    maker: str = ""
    date_part: str = ""
    rationale: str = ""
    try:
        maker = (ev.anchor.decision_maker or "").strip() if ev.anchor else ""
        ts = (ev.anchor.timestamp or "").strip() if ev.anchor else ""
        date_part = ts.split("T")[0] if ts else ""
        rationale = (ev.anchor.rationale or "").strip() if ev.anchor else ""
        if rationale and rationale[-1] in ".;,:":
            rationale = rationale[:-1].rstrip()
    except Exception:
        maker = ""
        date_part = ""
        rationale = ""
    # Compose the lead sentence.  When both maker and date are available
    # produce "<Maker> on <YYYY-MM-DD>: <rationale>.", otherwise just
    # "<rationale>.".  The rationale has been stripped of its trailing
    # punctuation above to avoid double periods.
    lead: str
    if maker and date_part:
        if rationale:
            lead = f"{maker} on {date_part}: {rationale}."
        else:
            lead = f"{maker} on {date_part}."
    else:
        lead = f"{rationale}." if rationale else ""
    lead = lead.strip()
    # Next pointer – use the first succeeding transition if available
    next_sent: str = ""
    try:
        suc = ev.transitions.succeeding or []
        if suc:
            first = suc[0]
            # transitions may be dicts or pydantic models; handle generically
            to_id = None
            try:
                # to field may be id of target node
                to_id = first.get("to") if isinstance(first, dict) else getattr(first, "to", None)
            except Exception:
                to_id = None
            # Resolve a human-friendly label for the next pointer.  **Never** emit raw IDs.
            label = None
            # Prefer an explicit `to_title` (enriched), then generic `title`
            title = None
            try:
                title = (first.get("to_title") if isinstance(first, dict) else getattr(first, "to_title", None)) \
                        or (first.get("title") if isinstance(first, dict) else getattr(first, "title", None))
            except Exception:
                title = None
            if title:
                label = title
            # If no title is available, attempt to use the `to` field only if it
            # is clearly *not* an ID (e.g., contains spaces). Otherwise, omit Next.
            if not label:
                if isinstance(to_id, str) and (" " in to_id.strip()):
                    label = to_id.strip()
            if label:
                next_sent = f" Next: {label}."
            else:
                try:
                    logger.info("templater.next_pointer_omitted", extra={"reason": "no_title_or_nonhuman_to"})
                except Exception:
                    pass
    except Exception:
        pass
    # Construct the provisional answer (lead + optional Next) and clamp to 320 characters
    answer = (lead + (next_sent or "")).strip()
    # Final clamp to max length
    if len(answer) > 320:
        answer = answer[:320]
    return answer.strip()

def finalise_short_answer(
    answer: WhyDecisionAnswer, evidence: WhyDecisionEvidence
) -> Tuple[WhyDecisionAnswer, bool]:
    """Post-process the short answer to remove stubs and enforce length.

    If the ``short_answer`` begins with the stub prefix ``"STUB ANSWER"``
    or is empty/None, synthesize a deterministic fallback answer based
    on the evidence.  Regardless of origin, truncate the short answer to
    320 characters.  Returns the (possibly modified) answer and a boolean
    indicating whether modifications were applied.
    """
    changed = False
    s = answer.short_answer or ""
    if not s or s.strip().upper().startswith("STUB ANSWER"):
        # Generate deterministic fallback via modern composer
        new_s = _compose_fallback_answer(evidence)
        if new_s != s:
            answer.short_answer = new_s
            changed = True
        s = new_s
    # Enforce maximum length
    if s and len(s) > 320:
        answer.short_answer = s[:320]
        changed = True
    return answer, changed