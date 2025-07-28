from collections import defaultdict
from typing import Dict, List, Iterable

# Simple alias map (can expand by observation in real pipelines)
ALIASES = {
    "option": ["title", "option", "decision", "choice"],
    "rationale": ["rationale", "why", "reasoning"],
    "summary": ["summary", "headline"],
    "reason": ["reason", "explanation"],
}

def build_field_catalog(decisions: Dict, events: Dict, transitions: Dict) -> Dict[str, List[str]]:
    # In M1 we publish a fixed alias mapping plus observed fields for proof
    observed = defaultdict(set)
    for obj in list(decisions.values()) + list(events.values()) + list(transitions.values()):
        for k in obj.keys():
            observed[k].add(k)

    catalog = {k: sorted(set(v) | observed.get(k, set())) for k, v in ALIASES.items()}
    # include core fields if missing
    core = ["id", "timestamp", "supported_by", "led_to", "transitions", "based_on", "tags", "from", "to", "relation", "snippet", "description", "decision_maker"]
    for k in core:
        catalog.setdefault(k, [k])
    # Promote previously unseen canonical fields so that every
    # observed key is surfaced as <canonical>: [<synonyms…>].
    for canon, syns in observed.items():
        if canon not in catalog:
            catalog[canon] = sorted(syns)
    return catalog

def build_relation_catalog() -> list[str]:
    return ["LED_TO", "CAUSAL_PRECEDES", "CHAIN_NEXT"]
