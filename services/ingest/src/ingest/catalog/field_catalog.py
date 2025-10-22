from collections import defaultdict
from typing import Dict, List, Set
from core_logging import get_logger, log_stage, current_request_id

logger = get_logger("ingest-catalog")

# Baseline alias map (can expand via observation)
ALIASES: Dict[str, List[str]] = {
    "option": ["title", "option", "decision", "choice"],
    "rationale": ["rationale", "why", "reasoning"],
    "summary": ["summary", "headline"],
    "reason": ["reason", "explanation"],
}

def _observe_keys(decisions: Dict[str, dict], events: Dict[str, dict]) -> Dict[str, Set[str]]:
    """
    Map canonical_key -> set(observed spellings).
    Canonicalization: lowercase the key for catalog purposes,
    but retain original spellings as synonyms.
    """
    observed: Dict[str, Set[str]] = defaultdict(set)
    for obj in list(decisions.values()) + list(events.values()):
        for k in obj.keys():
            observed[k.lower()].add(k)
    return observed

def build_field_catalog(decisions: Dict, events: Dict) -> Dict[str, List[str]]:
    """
    Self-learning alias catalog:
      - start from ALIASES
      - add observed keys and keep *all* observed spellings
      - ensure core keys exist with at least themselves as synonyms
      - deterministic sorting for stability
    """
    observed = _observe_keys(decisions, events)

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
        "id", "type", "timestamp", "domain", "sensitivity", "tags",
        "from", "to",
        "snippet", "description", "decision_maker",
    ]
    for k in core:
        catalog.setdefault(k, [k])

    log_stage(
        logger, "catalog", "field_catalog_built",
        canonical_count=len(catalog), observed_keys=len(observed),
        request_id=(current_request_id() or "unknown"),
    )
    return catalog

def build_relation_catalog() -> list[str]:
    """
    Canonical list of edge types used by ingest/UI.
    Prefer ontology if available; otherwise provide a safe baseline.
    The legacy 'CAUSAL_PRECEDES' alias must not be surfaced.
    """
    try:
        # Prefer the authoritative source if present
        from core_models.ontology import EDGE_TYPES  # type: ignore
        rels = [t for t in list(EDGE_TYPES) if str(t) != "CAUSAL_PRECEDES"]
    except (ImportError, AttributeError, NameError):
        # Baseline, conservative set
        rels = ["ALIAS_OF", "LED_TO", "CAUSAL"]
    # Deterministic order for stability
    return sorted(set(map(str, rels)), key=lambda s: (s.lower(), s))