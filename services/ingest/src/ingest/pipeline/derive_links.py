from typing import Dict

def derive_links(
    decisions: Dict[str, dict],
    events: Dict[str, dict],
    transitions: Dict[str, dict],
) -> None:
    """
    Derive reciprocal links:
      • event.led_to     ↔ decision.supported_by
      • transition.from/to ↔ decision.transitions
      • decision.based_on ↔ prior_decision.transitions
    """
    # event.led_to ↔ supported_by
    for eid, ev in events.items():
        for did in ev.get("led_to", []):
            decisions.setdefault(did, {}).setdefault("supported_by", []).append(eid)

    # transition.from/to ↔ transitions
    for tid, tr in transitions.items():
        fr, to = tr["from"], tr["to"]
        decisions.setdefault(fr, {}).setdefault("transitions", []).append(tid)
        decisions.setdefault(to, {}).setdefault("transitions", []).append(tid)

    # based_on ↔ prior_decision.transitions
    for did, dec in decisions.items():
        for prior in dec.get("based_on", []):
            decisions.setdefault(prior, {}).setdefault("transitions", []).append(did)