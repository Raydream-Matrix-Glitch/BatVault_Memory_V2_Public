from typing import List, Tuple
import re
from core_logging import get_logger
from core_models.models import WhyDecisionEvidence, WhyDecisionAnswer

logger = get_logger("templater")

def build_allowed_ids(ev: WhyDecisionEvidence) -> List[str]:
    """
    Compute a deterministic union of anchor, event and transition IDs.

    This helper has been updated to delegate to the core validator's
    canonical allowed‑ID computation.  It gathers the anchor ID, all
    event IDs and all transition IDs from the supplied evidence and
    returns them in canonical order (anchor first, then events by
    timestamp, then transitions by timestamp).  Duplicate IDs are
    removed.  The returned list may differ from the previous
    implementation's lexicographically sorted set.
    """
    from core_validator import canonical_allowed_ids

    # Collect plain dictionaries for events and transitions.  The
    # evidence model may contain pydantic objects; convert them to
    # dictionaries if necessary.
    ev_list: list[dict] = []
    for e in ev.events or []:
        if isinstance(e, dict):
            ev_list.append(e)
        else:
            try:
                ev_list.append(e.model_dump(mode="python"))
            except Exception:
                ev_list.append(dict(e))
    tr_list: list[dict] = []
    for t in list(getattr(ev.transitions, "preceding", []) or []) + list(getattr(ev.transitions, "succeeding", []) or []):
        if isinstance(t, dict):
            tr_list.append(t)
        else:
            try:
                tr_list.append(t.model_dump(mode="python"))
            except Exception:
                tr_list.append(dict(t))
    anchor_id = getattr(ev.anchor, "id", None) or ""
    return canonical_allowed_ids(anchor_id, ev_list, tr_list)

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

def _fallback_short_answer(ev: WhyDecisionEvidence) -> str:
    """Synthesize a deterministic fallback short answer.

    When the LLM is unavailable or returns a stub, the templater must
    construct a concise summary using the anchor rationale and an optional
    reference to the most recent event.  If no rationale is available
    the counts-based deterministic summary is used instead.  The result is
    truncated to 320 characters.
    """
    # Attempt to use the anchor's rationale when available
    rationale: str = ""
    try:
        rationale = (ev.anchor.rationale or "").strip()
    except Exception:
        rationale = ""
    # Determine counts for fallback in case rationale is empty
    def _etype(x):
        try:
            return (x.get("type") or x.get("entity_type") or "").lower()
        except Exception:
            return ""

    n_events = sum(1 for e in (ev.events or []) if _etype(e) == "event")
    n_decisions = sum(1 for e in (ev.events or []) if _etype(e) == "decision")
    n_pre = len(ev.transitions.preceding or [])
    n_suc = len(ev.transitions.succeeding or [])

    if not rationale:
        # No rationale – fall back to counts-based deterministic summary
        return _det_short_answer(
            ev.anchor.id if ev.anchor else "unknown",
            n_events,
            n_pre,
            n_suc,
            len(getattr(ev, "supporting_ids", []) or []),
            len(ev.allowed_ids or []),
        )
    # If there is a rationale, optionally append the latest event summary
    latest_event_summary: str | None = None
    # Identify the most recent event by timestamp (ISO strings compare lexicographically)
    try:
        sorted_events = sorted(
            [e for e in (ev.events or []) if isinstance(e, dict)],
            key=lambda e: e.get("timestamp") or "",
        )
        if sorted_events:
            last = sorted_events[-1]
            latest_event_summary = (
                last.get("summary")
                or last.get("description")
                or last.get("id")
            )
    except Exception:
        latest_event_summary = None
    # Compose the fallback.  Include counts as a parenthetical.
    fallback = rationale
    if latest_event_summary:
        fallback += f" Latest event: {latest_event_summary}."
    fallback += (
        f" ({n_events} event(s), {n_decisions} related decision(s), {n_pre} preceding, {n_suc} succeeding)."
    )
    return fallback[:320]

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
        # Generate deterministic fallback
        new_s = _fallback_short_answer(evidence)
        if new_s != s:
            answer.short_answer = new_s
            changed = True
        s = new_s
    # Enforce maximum length
    if s and len(s) > 320:
        answer.short_answer = s[:320]
        changed = True
    return answer, changed