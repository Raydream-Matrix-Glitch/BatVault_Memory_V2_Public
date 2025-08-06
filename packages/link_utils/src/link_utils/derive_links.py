from typing import Dict
import hashlib
from core_logging import get_logger, log_stage

logger = get_logger("link_utils")

def _dedup_sorted(ids):
    return sorted(set(i for i in ids if i))

def _fixup_id(src: str, rel: str, dst: str) -> str:
    raw = f"{src}|{rel}|{dst}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def derive_links(decisions: Dict[str, dict],
                 events: Dict[str, dict],
                 transitions: Dict[str, dict]) -> None:
    """
    Enforce reciprocal links with deterministic ordering:
      - event.led_to           <-> decision.supported_by
      - transition.from/to     <-> decision.transitions (transition IDs)
    """
    # event.led_to <-> decision.supported_by
    for eid, ev in events.items():
        for did in (ev.get("led_to", []) or []):
            if did in decisions:
                dec = decisions[did]
                before = set(dec.get("supported_by", []))
                after = set(before) | {eid}
                if after != before:
                    log_stage(logger, "derive_links", "link_fixup",
                              rel="supported_by", src=eid, dst=did,
                              fixup_id=_fixup_id(eid, "supported_by", did))
                dec["supported_by"] = _dedup_sorted(list(after))


    # transitions listed in both decisions
    for tid, tr in transitions.items():
        fr, to = tr.get("from"), tr.get("to")
        if fr in decisions:
            dd = decisions[fr]
            before = set(dd.get("transitions", []))
            after = set(before) | {tid}
            if after != before:
                log_stage(logger, "derive_links", "link_fixup",
                          rel="transitions", src=fr, dst=tid,
                          fixup_id=_fixup_id(fr, "transitions", tid))
            dd["transitions"] = _dedup_sorted(list(after))
        if to in decisions:
            dd = decisions[to]
            before = set(dd.get("transitions", []))
            after = set(before) | {tid}
            if after != before:
                log_stage(logger, "derive_links", "link_fixup",
                          rel="transitions", src=to, dst=tid,
                          fixup_id=_fixup_id(to, "transitions", tid))
            dd["transitions"] = _dedup_sorted(list(after))

    # decision.based_on <-> prior_decision.transitions
    for did, dec in decisions.items():
        for prior in (dec.get("based_on", []) or []):
            if prior in decisions:
                pd = decisions[prior]
                before = set(pd.get("transitions", []))
                after  = before | set(dec.get("transitions", []))
                if after != before:
                    log_stage(logger, "derive_links", "link_fixup",
                              rel="transitions", src=prior, dst=did,
                              fixup_id=_fixup_id(prior, "transitions", did))
                pd["transitions"] = _dedup_sorted(list(after))
