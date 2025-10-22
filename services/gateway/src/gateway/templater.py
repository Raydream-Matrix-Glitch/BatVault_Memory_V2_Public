from typing import List, Tuple
import re
from core_logging import get_logger, log_stage, current_request_id
from core_models_gen import WhyDecisionEvidence, WhyDecisionAnswer
from core_config.constants import (
    SHORT_ANSWER_MAX_CHARS as _CHAR_CAP_DEFAULT,
    SHORT_ANSWER_MAX_SENTENCES as _SENT_CAP_DEFAULT,
    KEY_EVENTS_COUNT as _KEY_EVENTS_COUNT,
)
from core_config import get_settings
from gateway.selector import rank_events as _rank_events
from core_utils.content import primary_text as _primary_text

logger = get_logger("gateway.templater")

def _resolve_caps() -> tuple[int, int]:
    s = get_settings()
    # Only honour the new names short_answer_max_chars and short_answer_max_sentences.  Do not
    char_env = getattr(s, "short_answer_max_chars", None)
    sent_env = getattr(s, "short_answer_max_sentences", None)
    try:
        char_cap = int(char_env) if char_env is not None else _CHAR_CAP_DEFAULT
    except (TypeError, ValueError):
        char_cap = _CHAR_CAP_DEFAULT
    try:
        sent_cap = int(sent_env) if sent_env is not None else _SENT_CAP_DEFAULT
    except (TypeError, ValueError):
        sent_cap = _SENT_CAP_DEFAULT
    return max(1, char_cap), max(1, sent_cap)

_ALIAS_RE = re.compile(r"^[AET]\d+$")

def _scrub_ids_from_text(text: str, allowed_ids: List[str]) -> str:
    """Remove any raw evidence IDs from free-form prose, **except** when they
    appear in explicit fields like "Decision ID: <id>" or "Event ID: <id>".

    Matches whole IDs only (IDs are slugs: letters, digits, dashes, underscores).
    We replace occurrences with an empty string and then normalise whitespace.
    """
    if not text:
        return text
    if not allowed_ids:
        return text
    cleaned = text
    # Protect ids that appear in explicit "Decision ID:" / "Event ID:" tails by
    # replacing them with temporary placeholders, running the scrub, then restoring.
    protected: dict[str, str] = {}
    idx = 0
    for _id in sorted(set([i for i in allowed_ids if isinstance(i, str)]), key=len, reverse=True):
        if not _id:
            continue
        # Protect "Decision ID: <id>"
        pat_dec = re.compile(rf"(Decision\s+ID:\s*){re.escape(_id)}(?![A-Za-z0-9_-])")
        token_dec = f"<<KEEP_DECISION_ID_{idx}>>"
        cleaned = pat_dec.sub(rf"\1{token_dec}", cleaned)
        protected[token_dec] = _id
        # Protect "Event ID: <id>"
        pat_evt = re.compile(rf"(Event\s+ID:\s*){re.escape(_id)}(?![A-Za-z0-9_-])")
        token_evt = f"<<KEEP_EVENT_ID_{idx}>>"
        cleaned = pat_evt.sub(rf"\1{token_evt}", cleaned)
        protected[token_evt] = _id
        idx += 1
    for _id in sorted(set([i for i in allowed_ids if isinstance(i, str)]), key=len, reverse=True):
        if not _id:
            continue
        # match when not part of a larger slug token
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(_id)}(?![A-Za-z0-9_-])")
        cleaned = pattern.sub("", cleaned)
    # Restore protected placeholders
    for token, original in protected.items():
        cleaned = cleaned.replace(token, original)
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
        f"Anchor {anchor_disp}: {events_n} event(s), "
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
        # v3 baseline: selection is derived from oriented graph.edges, not legacy transitions
        # - Use only causal edges (LED_TO | CAUSAL)
        # - Count preceding/succeeding only for edges that TOUCH THE ANCHOR (same-domain key context)
        ev: WhyDecisionEvidence = args[0]
        anchor_id = ev.anchor.id if ev.anchor else "unknown"
        edges = getattr(getattr(ev, "graph", None), "edges", None)
        edges = list(edges) if isinstance(edges, list) else []
        causal = [e for e in edges if str((e or {}).get("type") or "").upper() in {"LED_TO", "CAUSAL"}]
        def _touches_anchor(edge) -> bool:
            _from = (edge or {}).get("from")
            _to = (edge or {}).get("to")
            return _from == anchor_id or _to == anchor_id
        preceding_n = sum(1 for e in causal if (e or {}).get("orientation") == "preceding" and _touches_anchor(e))
        succeeding_n = sum(1 for e in causal if (e or {}).get("orientation") == "succeeding" and _touches_anchor(e))
        return _det_short_answer(
            anchor_id,
            len(ev.events or []),
            preceding_n,
            succeeding_n,
            len(getattr(ev, "cited_ids", []) or []),
            len(ev.allowed_ids or []),
        )
    return _det_short_answer(*args, **kwargs)

def validate_and_fix(
    answer: WhyDecisionAnswer, allowed_ids: List[str], anchor_id: str
) -> Tuple[WhyDecisionAnswer, bool, List[str]]:
    """Ensure cited_ids are a subset of allowed_ids and include the anchor.

    If any IDs are not in the allowed list they are removed; if the anchor
    ID is missing it is inserted at the front.  This helper does not perform
    the full contract enforcement; it merely applies legacy fixes.  The core
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
    return answer, changed, errs

def _compose_fallback_answer(ev: WhyDecisionEvidence) -> tuple[str, list[str]]:
    """Compose a deterministic, human-readable fallback answer.

    Two-step template:
      1) Lead: "<Maker> on DD Mon YYYY: <Title>." (or just "<Title>." if maker/date missing).
      2) If a succeeding transition exists, append " Next: <to_title>."
         Prefer `to_title`; fall back to a human-looking `to` string only if it is not an ID.
    Clamp to ≤320 chars and ≤2 sentences. Never emit raw IDs in the prose.
    """
    desc_txt: str = ""
    maker: str = ""
    date_part: str = ""
    title_txt: str = ""
    maker_obj = (ev.anchor.decision_maker if ev.anchor else None)
    maker = ""
    if isinstance(maker_obj, dict):
        maker = (maker_obj.get("name") or maker_obj.get("role") or maker_obj.get("id") or "").strip()
    elif isinstance(maker_obj, str):
        maker = maker_obj.strip()
    ts = (ev.anchor.timestamp or "").strip() if ev.anchor else ""
    date_part = ts.split("T")[0] if ts else ""
    title_txt = (ev.anchor.title or "").strip() if ev.anchor else ""
    if not title_txt and ev.anchor is not None and hasattr(ev.anchor, "model_dump"):
        try:
            title_txt = _primary_text(ev.anchor.model_dump(mode="python"))
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning("templater.primary_text.error", stage="templater", error=str(e))
            title_txt = ""
    if title_txt and title_txt[-1] in ".;,:":
        title_txt = title_txt[:-1].rstrip()
    desc_txt = (ev.anchor.description or "").strip() if ev.anchor else ""
    if desc_txt and desc_txt[-1] in ".;,:":
        desc_txt = desc_txt[:-1].rstrip()
    # Human-friendly date in "(DD Mon YYYY)" form
    def _fmt_date_iso(_ts: str) -> str:
        try:
            _date = (_ts or "").split("T")[0]
            if not _date:
                return ""
            parts = _date.split("-")
            if len(parts) != 3:
                return f"({_date})"
            y, m, d = parts
            months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            mm = int(m)
            dd = int(d)
            if 1 <= mm <= 12:
                return f"({dd} {months[mm-1]} {y})"
            return f"({_date})"
        except (ValueError, IndexError):
            return ""
    anchor_type = str(getattr(getattr(ev, "anchor", None), "type", "")).upper()
    _owner_role = ""
    if isinstance(maker_obj, dict):
        _owner_role = (maker_obj.get("role") or "").strip()
    date_human = _fmt_date_iso(ts) if ts else ""
    lead: str
    # Decision-anchored phrasing: "(15 Aug 2025) Sam Rivera: Title - Description."
    if anchor_type == "DECISION":
        prefix = (date_human + " ") if date_human else ""
        if maker:
            if title_txt and desc_txt:
                lead = f"{prefix}{maker}: {title_txt} - {desc_txt}."
            elif title_txt:
                lead = f"{prefix}{maker}: {title_txt}."
            else:
                lead = f"{prefix}{maker}."
        else:
            lead = (f"{prefix}{title_txt} - {desc_txt}." if (title_txt and desc_txt) else (f"{prefix}{title_txt}." if title_txt else prefix.strip()))
    # Event-anchored phrasing: acknowledge event explicitly
    elif anchor_type == "EVENT":
        prefix = (date_human + " ") if date_human else ""
        if title_txt and desc_txt:
            lead = f"{prefix}Event: {title_txt} - {desc_txt}."
        elif title_txt:
            lead = f"{prefix}Event: {title_txt}."
        else:
            lead = f"{prefix}Event."
    else:
        # Fallback to a neutral phrasing if the anchor type is unknown
        prefix = (date_human + " ") if date_human else ""
        if title_txt and desc_txt:
            lead = f"{prefix}{title_txt} - {desc_txt}."
        elif title_txt:
            lead = f"{prefix}{title_txt}."
        else:
            lead = prefix.strip()

    # Collect citations and optional because-clause
    cited_ids: list[str] = []
    anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
    if anchor_id:
        cited_ids.append(anchor_id)

    because_clause = ""
    anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
    edges_obj = getattr(getattr(ev, "graph", None), "edges", None)
    edges = list(edges_obj) if isinstance(edges_obj, list) else []
    into_anchor = {
        (e.get("from") or e.get("from_id"))
        for e in edges
        if str((e or {}).get("type") or "").upper() == "LED_TO" and e.get("to") == anchor_id
    }
    enriched: dict[str, dict] = {}
    for e in (getattr(ev, "events", None) or []):
        ed = e if isinstance(e, dict) else (e.model_dump(mode="python"))
        if str((ed.get("type") or ed.get("entity_type") or "")).upper() == "EVENT":
            _eid = ed.get("id")
            if isinstance(_eid, str):
                enriched[_eid] = ed
    events: list[dict] = [enriched.get(eid, {"id": eid}) for eid in into_anchor]
    try:
        ranked = _rank_events(ev.anchor, events)
    except (RuntimeError, ValueError, KeyError, TypeError):
        ranked = events
    k = int(_KEY_EVENTS_COUNT)
    k = max(0, k)
    anchor_domain = str(getattr(getattr(ev, "anchor", None), "domain", "") or "")
    chosen: list[dict] = []
    if k:
        for ed in ranked:
            ev_domain = str(ed.get("domain") or "")
            if anchor_domain and ev_domain and ev_domain != anchor_domain:
                log_stage(
                    logger, "templater", "templater.key_event_skipped_alias",
                    event_id=ed.get("id"), event_domain=ev_domain, anchor_domain=anchor_domain
                )
                continue
            chosen.append(ed)
            if len(chosen) >= k:
                break
    reasons: list[str] = []
    for ed in chosen:
        phrase = (ed.get("title") or ed.get("summary") or ed.get("snippet") or ed.get("description") or "").strip()
        if phrase and phrase[-1] in ".;,:":
            phrase = phrase[:-1].rstrip()
        if phrase:
            reasons.append(phrase)
        _eid = ed.get("id")
        if phrase and _eid and _eid in into_anchor and _eid not in cited_ids:
            cited_ids.append(_eid)
    if reasons:
        because_clause = " Key events: " + "; ".join(reasons) + "."
    # EVENT anchor: summarize decisions this event led to
    anchor_type = str(getattr(getattr(ev, "anchor", None), "type", "")).upper()
    if anchor_type == "EVENT":
        anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
        edges_obj = getattr(getattr(ev, "graph", None), "edges", None)
        edges = list(edges_obj) if isinstance(edges_obj, list) else []
        out_led = [
            ((e or {}).get("to"), (e or {}).get("timestamp") or "")
            for e in edges
            if str((e or {}).get("type") or "").upper() == "LED_TO" and ((e or {}).get("from") == anchor_id)
        ]
        out_led.sort(key=lambda t: (t[1], str(t[0] or "")), reverse=True)
        titles: list[str] = []
        for to_id, _ts in out_led[:3]:
            if not to_id:
                continue
            label = None
            for _n in (getattr(ev, "events", []) or []):
                if isinstance(_n, dict) and (_n.get("id") == to_id):
                    label = _n.get("title") or _n.get("description")
                    break
            if isinstance(label, str) and label.strip():
                t = label.strip()
                if t and t[-1] in ".;,:":
                    t = t[:-1].rstrip()
                if t and t not in titles:
                    titles.append(t)
            if to_id and to_id not in cited_ids:
                cited_ids.append(to_id)
        if titles:
            led_to_clause = " Led to: " + "; ".join(titles) + "."
        elif out_led:
            n = len(out_led)
            led_to_clause = f" Led to: {n} decision{'s' if n != 1 else ''}."
        else:
            led_to_clause = ""
    else:
        led_to_clause = ""
    # Next pointer – choose a succeeding *causal* edge touching the anchor (same-domain).
    # Alias-tail edges (alias_event → next_decision) are oriented succeeding but do NOT touch the anchor;
    # they are for cross-domain impact segments and are not used for this inline "Next:" pointer.
    next_sent: str = ""
    try:
        anchor_id = getattr(getattr(ev, "anchor", None), "id", None)
        edges = list((getattr(getattr(ev, "graph", None), "edges", None) or []))
        causal = [e for e in edges if str((e or {}).get("type") or "").upper() == "CAUSAL"]
        touching_succeeding = [
            e for e in causal
            if (e or {}).get("orientation") == "succeeding"
            and (((e or {}).get("from") == anchor_id) or ((e or {}).get("to") == anchor_id))
        ]
        if touching_succeeding:
            first = touching_succeeding[0]
            to_id = (first or {}).get("to")
            # Edges are strict per baseline; they won't carry titles. Avoid emitting raw IDs.
            # If a human label has been attached upstream (rare), use it; otherwise omit.
            title = None
            try:
                # Non-normative grace: tolerate optional enrichment keys if present (will be absent in strict views).
                title = (first or {}).get("to_title") or (first or {}).get("title")
            except Exception:
                title = None
            # Fallback: consult evidence.events (bounded enrich) for a human title/description for `to_id`
            if not (isinstance(title, str) and title.strip()):
                try:
                    for _n in (getattr(ev, "events", []) or []):
                        if isinstance(_n, dict) and (_n.get("id") == to_id):
                            title = _n.get("title") or _n.get("description")
                            break
                except Exception:
                    title = title
            label = title.strip() if isinstance(title, str) and title.strip() else None
            if label:
                next_sent = f" Next: {label}."
                if isinstance(to_id, str) and to_id not in cited_ids:
                    cited_ids.append(to_id)
            else:
                log_stage(logger, "templater", "templater.next_pointer_omitted",
                          reason="no_human_label_on_edge",
                          request_id=(current_request_id() or "unknown"))
    except Exception:
        pass
    # Construct answer – append because-clause and Next if within caps (env-driven)
    char_cap, sent_cap = _resolve_caps()

    answer = lead
    # Append event-led_to clause first for EVENT anchors
    if led_to_clause and len(answer) + len(led_to_clause) <= char_cap:
        answer = (answer + led_to_clause).strip()
    if because_clause and len(answer) + len(because_clause) <= char_cap:
        answer = (answer + because_clause).strip()
    if next_sent and len(answer) + len(next_sent) <= char_cap:
        answer = (answer + next_sent).strip()
        log_stage(logger, "templater", "templater.next_pointer_added",
                  lead_len=len(lead), next_len=len(next_sent),
                  request_id=(current_request_id() or "unknown"))
    else:
        pass

    # Owner/Decision ID tail for DECISION anchors
    owner_tail = ""
    if anchor_type == "DECISION":
        _owner_name = maker.strip() if isinstance(maker, str) else ""
        _role = _owner_role
        _id = anchor_id
        if _owner_name:
            if _role:
                owner_tail = f" Owner: {_owner_name} ({_role})"
            else:
                owner_tail = f" Owner: {_owner_name}"
        if isinstance(_id, str) and _id.strip():
            if owner_tail:
                owner_tail = owner_tail + f" – Decision ID: {_id}."
            else:
                owner_tail = f" Decision ID: {_id}."
        if owner_tail and len(answer) + len(owner_tail) <= char_cap:
            answer = (answer + owner_tail).strip()

    # Event ID tail for EVENT anchors (explicit and placed at the very end)
    if anchor_type == "EVENT":
        _eid = anchor_id
        if isinstance(_eid, str) and _eid.strip():
            eid_tail = f" Event ID: {_eid}."
            if len(answer) + len(eid_tail) <= char_cap:
                answer = (answer + eid_tail).strip()

    # If enrichment failed and we could not compose reasons/next, make it explicit (no IDs leaked).
    try:
        if getattr(ev, "_enrich_failed", False) and (not because_clause) and (not next_sent):
            err_note = " (enrichment unavailable)"
            if len(answer) + len(err_note) <= char_cap:
                answer = (answer + err_note).strip()
    except Exception:
        pass
    # No teaser tails; keep answer deterministic and self-contained
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
        answer.cited_ids = list(planned_citations)
        log_stage(logger, "templater", "templater.citations_planned", count=len(planned_citations))
        s = new_s
    # Scrub any raw evidence IDs from prose (post-model safety)
    allowed = list(getattr(evidence, 'allowed_ids', []) or [])
    if not allowed:
        anchor_id = getattr(getattr(evidence, 'anchor', None), 'id', '')
        events = []
        for e in (getattr(evidence, 'events', []) or []):
            if isinstance(e, dict):
                events.append(e)
            elif hasattr(e, 'model_dump'):
                events.append(e.model_dump())
        allowed = list((getattr(evidence, 'allowed_ids', []) or []) or [anchor_id])
    _hits = []
    for _id in allowed:
        if isinstance(_id, str) and re.search(rf'(?<![A-Za-z0-9_-]){re.escape(_id)}(?![A-Za-z0-9_-])', s):
            _hits.append(_id)
    if _hits:
        log_stage(logger, "templater", "safety.scrub_ids", hit_count=len(_hits))
    scrubbed = _scrub_ids_from_text(s, allowed)
    if scrubbed != s:
        s = scrubbed
        answer.short_answer = scrubbed
        changed = True
    # Append a one-liner permission note when the policy withheld items.
    if append_permission_note:
        note = " Note: Some evidence was withheld due to your permissions."
        if isinstance(s, str) and note.strip() not in s:
            s = (s or "") + note
            log_stage(logger, "templater", "templater.permission_note_appended", appended=True)
# Enforce sentence count and maximum length (env-driven; canonical with legacy fallback)
    char_cap, sent_cap = _resolve_caps()
    if s:
        # Sentence clamp first, then character clamp
        parts = re.split(r'(?<=[.!?])\s+', s.strip())
        if len(parts) > sent_cap:
            s_new = " ".join(parts[:sent_cap]).strip()
            if s_new != s:
                log_stage(logger, "templater", "templater.sentences_clamped", **{"from": len(parts), "to": sent_cap})
                s = s_new
        if len(s) > char_cap:
            s = s[:char_cap]
            log_stage(logger, "templater", "templater.chars_clamped", to=char_cap)
        if s != answer.short_answer:
            answer.short_answer = s
            changed = True
    return answer, changed