from typing import Tuple, Any, Dict
from core_logging import get_logger
from core_models_gen import WhyDecisionEvidence, WhyDecisionAnswer
from core_models_gen.models import AnswerBlocks, AnswerOwner
from core_models.ontology import canonical_edge_type

logger = get_logger("gateway.templater")

def build_answer_blocks(evidence: WhyDecisionEvidence) -> AnswerBlocks:
    """
    Compose schema-based answer blocks from evidence deterministically.
    - lead: "<Maker> on <YYYY-MM-DD>: <Title>." (or "<Title>." if maker/date missing)
    - description: anchor.description (trimmed)
    - key_events: up to 3 *items* from preceding LED_TO edges to the anchor
                 (prefer titles; fallback to IDs)
    - next: succeeding CAUSAL transition from the anchor
            (prefer title; fallback to ID)
    - owner: { name, role } if available
    - decision_id: anchor.id if present
    """
    ev = evidence
    # maker: support attribute or dict (pydantic v2 extras live in model_dump)
    maker_obj = None
    if ev.anchor is not None:
        maker_obj = getattr(ev.anchor, "decision_maker", None)
        if maker_obj is None and hasattr(ev.anchor, "model_dump"):
            _raw = ev.anchor.model_dump(mode="python")
            if isinstance(_raw, dict):
                maker_obj = _raw.get("decision_maker")
    maker_name = None
    maker_role = None
    if isinstance(maker_obj, dict):
        maker_name = (maker_obj.get("name") or maker_obj.get("id") or maker_obj.get("role") or "").strip() or None
        maker_role = (maker_obj.get("role") or "").strip() or None
    elif isinstance(maker_obj, str):
        maker_name = maker_obj.strip() or None
    # date (attribute or dict)
    ts = (getattr(ev.anchor, "timestamp", "") or "").strip() if ev.anchor else ""
    if not ts and ev.anchor is not None and hasattr(ev.anchor, "model_dump"):
        _raw = ev.anchor.model_dump(mode="python")
        if isinstance(_raw, dict):
            ts = (_raw.get("timestamp") or "").strip()
    date_part = ts.split("T")[0] if ts else ""
    # title + description (attribute first, then dict)
    title_txt = (getattr(ev.anchor, "title", "") or "").strip() if ev.anchor else ""
    if title_txt and title_txt[-1] in ".;,:":
        title_txt = title_txt[:-1].rstrip()
    desc_txt = (getattr(ev.anchor, "description", "") or "").strip() if ev.anchor else ""
    if not desc_txt and ev.anchor is not None and hasattr(ev.anchor, "model_dump"):
        _raw = ev.anchor.model_dump(mode="python")
        if isinstance(_raw, dict):
            desc_txt = (_raw.get("description") or "").strip()
    if desc_txt and desc_txt[-1] in ".;,:":
        desc_txt = desc_txt[:-1].rstrip()
    # key_events via LED_TO
    key_events: list[str] = []
    # Robust anchor id extraction (str | dict | model)
    anchor_id = None
    _a = getattr(ev, "anchor", None)
    if isinstance(_a, str):
        anchor_id = _a
    elif isinstance(_a, dict):
        anchor_id = _a.get("id")
    else:
        anchor_id = getattr(_a, "id", None)
    edges_obj = getattr(getattr(ev, "graph", None), "edges", None)
    edges = list(edges_obj) if isinstance(edges_obj, list) else []
    def _et(e):
        try: return canonical_edge_type((e or {}).get("type"))
        except ValueError: return None
    in_led = [((e or {}).get("from"), (e or {}).get("timestamp") or "") for e in edges
              if _et(e) == "LED_TO"
              and str((e or {}).get("orientation") or "").lower() == "preceding"
              and ((e or {}).get("to") == anchor_id)]
    in_led.sort(key=lambda t: (t[1], str(t[0] or "")), reverse=True)
    titles: list[str] = []
    fallback_ids: list[str] = []
    for from_id, _ts in in_led[:3]:
        if not from_id:
            continue
        # Always record the id fallback in order (dedupe later)
        sid = str(from_id)
        if sid not in fallback_ids:
            fallback_ids.append(sid)
        label = None
        for _n in (getattr(ev, "events", []) or []):
            if isinstance(_n, dict) and (_n.get("id") == from_id):
                label = _n.get("title") or _n.get("description")
                break
        if isinstance(label, str) and label.strip():
            t = label.strip()
            if t and t[-1] in ".;,:":
                t = t[:-1].rstrip()
            if t and t not in titles:
                titles.append(t)
    # Prefer titles; fall back to IDs (so FE can hydrate)
    key_events = titles if titles else (fallback_ids or None)
    # next via succeeding CAUSAL
    next_title = None
    edges_succ = [e for e in edges
                  if _et(e) == "CAUSAL"
                  and str((e or {}).get("orientation") or "").lower() == "succeeding"
                  and ((e or {}).get("from") == anchor_id)]
    edges_succ.sort(key=lambda e: (e.get("timestamp") or "", str(e.get("to") or "")), reverse=True)
    if edges_succ:
        to_id = edges_succ[0].get("to")
        for _n in (getattr(ev, "events", []) or []):
            if isinstance(_n, dict) and (_n.get("id") == to_id):
                next_title = (_n.get("title") or _n.get("description") or "").strip() or None
                if next_title and next_title[-1] in ".;,:":
                    next_title = next_title[:-1].rstrip()
                break
        # If we couldn't resolve a title/description, fall back to the ID so FE can hydrate
        if not next_title and to_id:
            next_title = str(to_id)
    # lead & owner
    if maker_name and date_part:
        lead = f"{maker_name} on {date_part}: {title_txt or (str(anchor_id) if anchor_id else '—')}."
    else:
        lead = f"{(title_txt or (str(anchor_id) if anchor_id else '—'))}."
    owner = AnswerOwner(name=maker_name, role=maker_role) if maker_name else None
    return AnswerBlocks(
        lead=lead,
        description=(desc_txt or None),
        key_events=key_events,
        next=next_title,
        owner=owner,
        decision_id=(str(anchor_id) if anchor_id else None),
    )

def _apply_constraints(blocks: dict, tmpl: dict) -> dict:
    """
    Enforce simple constraints from the template (max chars/items, required-presence).
    Never fabricate content; only trim.
    """
    out = dict(blocks)
    cons: Dict[str, Any] = dict(tmpl.get("constraints") or {})
    # Strings: max_chars
    for k in ("lead", "description"):
        if k in out and isinstance(out[k], str):
            try:
                mx = int(((cons.get(k) or {}).get("max_chars") or 0))
            except (TypeError, ValueError):
                mx = 0
            if mx > 0 and len(out[k]) > mx:
                out[k] = out[k][:mx].rstrip()
    # Lists: key_events.max_items
    if "key_events" in out and isinstance(out["key_events"], list):
        try:
            mi = int(((cons.get("key_events") or {}).get("max_items") or -1))
        except (TypeError, ValueError):
            mi = -1
        if mi >= 0:
            out["key_events"] = out["key_events"][:mi]
    # Presence constraints are enforced by omission (we do not fabricate).
    return out

def apply_template(blocks: AnswerBlocks, template: dict) -> AnswerBlocks:
    """
    Deterministically select/order/trim AnswerBlocks per template.
    - Only include declared blocks, in declared order.
    - Apply constraints (no fabrication).
    """
    want = [str(x) for x in (template.get("blocks") or [])]
    raw = dict(blocks.model_dump(mode="python", exclude_none=True))
    raw = _apply_constraints(raw, template)
    filtered: Dict[str, Any] = {}
    for name in want:
        if name in raw and raw[name] not in (None, "", [], {}):
            filtered[name] = raw[name]
    return AnswerBlocks(**filtered)

def finalise_answer_blocks(answer: WhyDecisionAnswer, evidence: WhyDecisionEvidence) -> tuple[WhyDecisionAnswer, bool]:
    """
    Hard cut-over to schema blocks: rebuild deterministically from evidence.
    """
    blocks = build_answer_blocks(evidence)
    if getattr(answer, "blocks", None) != blocks:
        answer.blocks = blocks
        return answer, True
    return answer, False
