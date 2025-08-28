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
    """Synthesize a deterministic fallback short answer (legacy).

    Historically the templater produced a counts-based fallback when no
    rationale was available.  This helper is retained for backward
    compatibility but is no longer used by the Gateway.  It remains
    exposed for unit-tests that depend on its previous behaviour.  See
    `_compose_fallback_answer` for the modern deterministic fallback.
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
    # If there is a rationale, append up to three most recent event summaries.
    event_summaries: list[str] = []
    try:
        sorted_events = sorted(
            [e for e in (ev.events or []) if isinstance(e, dict)],
            key=lambda e: e.get("timestamp") or ""
        )
        # take up to three most recent events
        for e in sorted_events[-3:]:
            s = e.get("summary") or e.get("description") or e.get("id")
            if s:
                event_summaries.append(s)
    except Exception:
        pass

    fallback = ("Rationale: " + rationale) if rationale else ""
    if event_summaries:
        fallback += " Key events: " + "; ".join(event_summaries) + "."
    # Build compact counts; omit zero categories.
    parts: list[str] = []
    if n_events:
        parts.append(f"{n_events} event" + ("" if n_events == 1 else "s"))
    trans_bits: list[str] = []
    if n_pre:
        trans_bits.append(f"{n_pre} preceding")
    if n_suc:
        trans_bits.append(f"{n_suc} succeeding")
    if trans_bits:
        total_trans = n_pre + n_suc
        parts.append(", ".join(trans_bits) + " transition" + ("" if total_trans == 1 else "s"))
    if parts:
        fallback += " (" + "; ".join(parts) + ")."
    return fallback[:320]

def _compose_fallback_answer(ev: WhyDecisionEvidence) -> str:
    """Compose a deterministic, human‑readable fallback answer.

    This pure helper synthesises a concise two‑sentence explanation when the
    LLM is disabled, unhealthy or returns an output that violates the
    style specification.  It follows a strict template:

      1. If a decision maker and timestamp are present on the anchor then
         the lead sentence begins with "<Maker> on <YYYY-MM-DD>: <rationale>.".
         Otherwise the rationale alone forms the lead sentence.
      2. If there is at least one succeeding transition, append a second
         sentence "Next: <to_id or title>." referencing the first
         succeeding transition's id or its ``to`` field.  If no succeeding
         transitions exist, the second sentence is omitted.
      3. If there are events present then append a final clause
         " Key events: A; B; C." containing up to three of the most
         recent event summaries (most recent first).  This clause is
         omitted if the overall answer would exceed 320 characters.

    The final answer is clamped to a maximum of 320 characters and at
    most two sentences (the optional "Key events" clause counts as part
    of the second sentence).  Raw evidence IDs must not appear in
    the prose; callers should strip them prior to invocation.
    """
    # Extract maker and date from the anchor if available
    maker: str = ""
    date_part: str = ""
    rationale: str = ""
    try:
        maker = (ev.anchor.decision_maker or "").strip() if ev.anchor else ""
        ts = (ev.anchor.timestamp or "").strip() if ev.anchor else ""
        date_part = ts.split("T")[0] if ts else ""
        rationale = (ev.anchor.rationale or "").strip() if ev.anchor else ""
    except Exception:
        maker = ""
        date_part = ""
        rationale = ""
    # Lead sentence
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
            # fallback to id/title
            label = None
            # Prefer the explicit `to` field for the transition pointer.  Only
            # fall back to a title when no `to` is provided.  We do not
            # surface raw transition IDs here per spec – identifiers must
            # never appear in the prose.
            if to_id:
                label = to_id
            else:
                # Attempt to use a human-readable title when available
                title = None
                try:
                    title = first.get("title") if isinstance(first, dict) else getattr(first, "title", None)
                except Exception:
                    title = None
                if title:
                    label = title
            if label:
                next_sent = f" Next: {label}."
    except Exception:
        pass
    # Key events clause – gather up to three most recent events (most recent first)
    key_events: str = ""
    try:
        events = [e for e in (ev.events or []) if isinstance(e, dict)]
        # sort by timestamp ascending then take last 3 reversed
        events_sorted = sorted(events, key=lambda e: e.get("timestamp") or "")
        most_recent = list(reversed(events_sorted[-3:]))
        names: list[str] = []
        for e in most_recent:
            # prefer summary, then description, then id
            s = e.get("summary") or e.get("description") or e.get("title") or e.get("id")
            if s:
                names.append(str(s))
        if names:
            key_events = f" Key events: {'; '.join(names)}."
    except Exception:
        key_events = ""
    # Construct the provisional answer and clamp to 320 characters
    answer = lead + (next_sent or "") + (key_events or "")
    # If the key_events clause pushes us over 320 chars remove it entirely
    if len(answer) > 320 and key_events:
        answer = (lead + (next_sent or "")).strip()
    # Clamp to max length
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
        try:
            new_s = _compose_fallback_answer(evidence)
        except Exception:
            # fallback to legacy if composer fails
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