from collections import defaultdict
from typing import Dict, List, Set
from core_logging import get_logger, log_stage

logger = get_logger("ingest-catalog")

# Baseline alias map (can expand via observation)
ALIASES: Dict[str, List[str]] = {
    "option": ["title", "option", "decision", "choice"],
    "rationale": ["rationale", "why", "reasoning"],
    "summary": ["summary", "headline"],
    "reason": ["reason", "explanation"],
}

def _observe_keys(decisions: Dict[str, dict], events: Dict[str, dict], transitions: Dict[str, dict]) -> Dict[str, Set[str]]:
    """
    Map canonical_key -> set(observed spellings).
    Canonicalization: lowercase the key for catalog purposes,
    but retain original spellings as synonyms.
    """
    observed: Dict[str, Set[str]] = defaultdict(set)
    for obj in list(decisions.values()) + list(events.values()) + list(transitions.values()):
        for k in obj.keys():
            observed[k.lower()].add(k)
    return observed

def build_field_catalog(decisions: Dict, events: Dict, transitions: Dict) -> Dict[str, List[str]]:
    """
    Self-learning alias catalog:
      - start from ALIASES
      - add observed keys and keep *all* observed spellings
      - ensure core keys exist with at least themselves as synonyms
      - deterministic sorting for stability
    """
    observed = _observe_keys(decisions, events, transitions)

    # Start with baseline aliases and union observed spellings for same canonical key
    catalog: Dict[str, List[str]] = {}
    for canon, syns in ALIASES.items():
        union: Set[str] = set(syns) | observed.get(canon, set())
        catalog[canon] = sorted(union, key=lambda s: (s.lower(), s))

    # Promote previously unseen canonical fields with their observed spellings
    for canon, syns in observed.items():
        if canon not in catalog:
            catalog[canon] = sorted(syns, key=lambda s: (s.lower(), s))

    # Include core fields if missing
    core = [
        "id", "timestamp", "supported_by", "led_to",
        "from", "to", "relation",
        "snippet", "description", "decision_maker",
    ]
    for k in core:
        catalog.setdefault(k, [k])

    log_stage(logger, "catalog", "field_catalog_built",
              canonical_count=len(catalog), observed_keys=len(observed))
    return catalog

def build_relation_catalog() -> list[str]:
    return ["LED_TO", "CAUSAL_PRECEDES", "CHAIN_NEXT", "ALIAS_OF"]