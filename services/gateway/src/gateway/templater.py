from typing import List, Tuple
import re
from core_logging import get_logger
from core_models.models import WhyDecisionEvidence, WhyDecisionAnswer
from core_validator import canonical_allowed_ids
from core_config import get_settings
from core_config.constants import (
    SHORT_ANSWER_MAX_CHARS as _CHAR_CAP_DEFAULT,
    SHORT_ANSWER_MAX_SENTENCES as _SENT_CAP_DEFAULT,
)
from gateway import selector as _selector

logger = get_logger("gateway.templater")

def _resolve_caps() -> tuple[int, int]:
    s = get_settings()
    char_cap = int(getattr(s, "short_answer_max_chars", None) or getattr(s, "answer_char_cap", None) or _CHAR_CAP_DEFAULT)
    sent_cap = int(getattr(s, "short_answer_max_sentences", None) or getattr(s, "answer_sentence_cap", None) or _SENT_CAP_DEFAULT)
    return max(1, char_cap), max(1, sent_cap)

_ALIAS_RE = re.compile(r"^[AET]\d+$")

def _scrub_ids_from_text(text: str, allowed_ids: List[str]) -> str:
    """Remove any raw evidence IDs from free-form prose.

    Matches whole IDs only (IDs are slugs: letters, digits, dashes, underscores).
    We replace occurrences with an empty string and then normalise whitespace.
    """
    if not text:
        return text
    if not allowed_ids:
        return text
    cleaned = text
    for _id in sorted(set([i for i in allowed_ids if isinstance(i, str)]), key=len, reverse=True):
        if not _id:
            continue
        # match when not part of a larger slug token
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(_id)}(?![A-Za-z0-9_-])")
        cleaned = pattern.sub("", cleaned)
    # collapse excessive whitespace and fix spaces before punctuation
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned

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
    )[:_resolve_caps()[0]]

def deterministic_short_answer(*args, **kwargs):  # type: ignore[override]
    """Polymorphic deterministic short answer.

    When passed a WhyDecisionEvidence instance as the first argument, this
    helper computes a counts-based summary derived from the evidence.
    When passed explicit numeric arguments (anchor_id, events_n, …), it
    defers to the counts-based version.  Truncation uses the configured character cap
    is applied uniformly.
    """
    if args and isinstance(args[0], WhyDecisionEvidence):
        ev: WhyDecisionEvidence = args[0]
        return _det_short_answer(
            ev.anchor.id if ev.anchor else "unknown",
            len(ev.events or []),
            len(getattr(ev.transitions, "preceding", []) or []),
            len(getattr(ev.transitions, "succeeding", []) or []),
            len(getattr(ev, "cited_ids", []) or []),
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
    orig_support = list(answer.cited_ids or [])
    support = [x for x in orig_support if x in allowed]
    changed = len(support) != len(orig_support)
    if anchor_id not in support:
        support = [anchor_id] + [x for x in support if x != anchor_id]
        changed = True
    errs: List[str] = []
    if changed:
        errs.append("cited_ids adjusted to fit allowed_ids and include anchor")
    answer.cited_ids = support
    # Back-compat: keep legacy field mirrored during migration
    try:
        answer.supporting_ids = list(support)
    except Exception:
        pass
    return answer, changed, errs

def _compose_fallback_answer(ev: WhyDecisionEvidence) -> tuple[str, list[str]]:
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

    # Collect citations and optional because-clause
    cited_ids: list[str] = []
    try:
        anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
        if anchor_id:
            cited_ids.append(anchor_id)
    except Exception:
        pass

    because_clause = ""
    try:
        # Normalise events to dicts and rank them; then take top-k for the clause
        events: list[dict] = []
        for e in (ev.events or []):
            ed = e if isinstance(e, dict) else (e.model_dump(mode="python"))
            # Only consider real event nodes
            if (ed.get("type") or ed.get("entity_type") or "").lower() == "event":
                events.append(ed)
        ranked = _selector.rank_events(ev.anchor, events)
        try:
            s = get_settings()
            k = int(getattr(s, "because_event_count", 2))
        except Exception:
            k = 2
        chosen = ranked[:max(0, k)]
        reasons: list[str] = []
        for ed in chosen:
            # Prefer concise text; trim trailing punctuation to avoid ".."
            phrase = (ed.get("summary") or ed.get("snippet") or ed.get("description") or "").strip()
            if phrase and phrase[-1] in ".;,:":
                phrase = phrase[:-1].rstrip()
            if phrase:
                reasons.append(phrase)
            _eid = ed.get("id")
            if _eid and _eid not in cited_ids:
                cited_ids.append(_eid)
        if reasons:
            because_clause = " Because " + "; ".join(reasons) + "."
    except Exception:
        because_clause = ""
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
                try:
                    # record cited transition id
                    _tid = to_id if isinstance(to_id, str) else None
                    if _tid and _tid not in cited_ids:
                        cited_ids.append(_tid)
                except Exception:
                    pass
            else:
                try:
                    logger.info("templater.next_pointer_omitted", extra={"reason": "no_title_or_nonhuman_to"})
                except Exception:
                    pass
    except Exception:
        pass
    # Construct answer – append because-clause and Next if within caps (env-driven)
    char_cap, sent_cap = _resolve_caps()

    answer = lead
    if because_clause and len(answer) + len(because_clause) <= char_cap:
        answer = (answer + because_clause).strip()
    if next_sent and len(answer) + len(next_sent) <= char_cap:
        answer = (answer + next_sent).strip()
        try:
            logger.info("templater.next_pointer_added", extra={"lead_len": len(lead), "next_len": len(next_sent)})
        except Exception:
            pass
    else:
        answer = lead

    # Optional tail to hint at more context
    try:
        remaining = max(0, len(set(getattr(ev, "allowed_ids", []) or [])) - len(set(cited_ids)))
    except Exception:
        remaining = 0
    tail = " More in timeline." if remaining > 0 else ""
    if tail and len(answer) + len(tail) <= char_cap:
        answer = (answer + tail).strip()
        try:
            logger.info("templater.tail_added", extra={"remaining": remaining})
        except Exception:
            pass
    if len(answer) > char_cap:
        answer = answer[:char_cap]
    return answer.strip(), cited_ids

def finalise_short_answer(
    answer: WhyDecisionAnswer,
    evidence: WhyDecisionEvidence,
    append_permission_note: bool = False,
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
        # Generate deterministic fallback via modern composer (also returns planned citations)
        (new_s, planned_citations) = _compose_fallback_answer(evidence)
        if new_s != s:
            answer.short_answer = new_s
            changed = True
        # Mirror planned citations to both fields
        try:
            answer.cited_ids = list(planned_citations)
        except Exception:
            pass
        try:
            answer.supporting_ids = list(planned_citations)
        except Exception:
            pass
        try:
            logger.info("templater.citations_planned", extra={"count": len(planned_citations)})
        except Exception:
            pass
        s = new_s
    # Scrub any raw evidence IDs from prose (post-model safety)
    try:
        allowed = list(getattr(evidence, 'allowed_ids', []) or [])
        if not allowed:
            # fall back to canonical build if evidence.allowed_ids is empty
            anchor_id = getattr(getattr(evidence, 'anchor', None), 'id', '')
            events = []
            for e in (getattr(evidence, 'events', []) or []):
                if isinstance(e, dict):
                    events.append(e)
                else:
                    try:
                        events.append(e.model_dump())
                    except Exception:
                        pass
            trans = []
            trans_obj = getattr(evidence, 'transitions', None)
            if trans_obj:
                for _k in ('preceding', 'succeeding'):
                    try:
                        arr = getattr(trans_obj, _k, []) or []
                        for t in arr:
                            if isinstance(t, dict):
                                trans.append(t)
                            else:
                                try:
                                    trans.append(t.model_dump())
                                except Exception:
                                    pass
                    except Exception:
                        pass
            allowed = canonical_allowed_ids(anchor_id, events, trans)
        # detect which IDs appear in prose before scrubbing (for logs)
        try:
            _hits = []
            for _id in allowed:
                try:
                    if re.search(rf'(?<![A-Za-z0-9_-]){re.escape(_id)}(?![A-Za-z0-9_-])', s):
                        _hits.append(_id)
                except Exception:
                    pass
            if _hits:
                try:
                    logger.info('safety.scrub_ids', extra={'hit_count': len(_hits)})
                except Exception:
                    pass
        except Exception:
            pass
        scrubbed = _scrub_ids_from_text(s, allowed)
        if scrubbed != s:
            s = scrubbed
            answer.short_answer = scrubbed
            changed = True
    except Exception:
        pass
# Append a one-liner permission note when the policy withheld items.
        try:
            if append_permission_note:
                note = " Note: Some evidence was withheld due to your permissions."
                # Append only if not already present (idempotent).
                if isinstance(s, str) and note.strip() not in s:
                    s_before = s
                    s = (s or "") + note
                    try:
                        logger.info("templater.permission_note_appended", extra={"appended": True})
                    except Exception:
                        pass
        except Exception:
            pass
# Enforce sentence count and maximum length (env-driven; canonical with legacy fallback)
    char_cap, sent_cap = _resolve_caps()
    if s:
        # Sentence clamp first, then character clamp
        parts = re.split(r'(?<=[.!?])\s+', s.strip())
        if len(parts) > sent_cap:
            s_new = " ".join(parts[:sent_cap]).strip()
            if s_new != s:
                try:
                    logger.info("templater.sentences_clamped", extra={"from": len(parts), "to": sent_cap})
                except Exception:
                    pass
                s = s_new
        if len(s) > char_cap:
            s = s[:char_cap]
            try:
                logger.info("templater.chars_clamped", extra={"to": char_cap})
            except Exception:
                pass
        if s != answer.short_answer:
            answer.short_answer = s
            changed = True
    return answer, changed