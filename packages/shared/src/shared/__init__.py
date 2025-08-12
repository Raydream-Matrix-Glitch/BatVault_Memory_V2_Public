"""
Shared normalisation utilities for BatVault nodes.

This package exposes helper functions to apply consistent, canonical
normalisation rules to decision, event and transition nodes.  The
functions are pure and avoid any side effects such as database
interaction.  They enforce ISO‑8601 timestamps, guarantee the
presence of an ``x-extra`` block, underscore hyphenated tags and
filter out unknown attributes.  When a node lacks a ``type``
declaration the functions will populate one based on the high‑level
context.

The intention is for both ingestion pipelines and API layers to use
the same normalisation logic.  This prevents drift between systems
and ensures that documents inserted into storage adhere to a single
schema from the outset.  Downstream services should therefore
not reapply normalisation – at most they may perform a light guard
around externally supplied objects.
"""

from .normalize import (
    normalize_event,
    normalize_timestamp,
    normalize_decision,
    normalize_transition,
)

__all__ = [
    "normalize_event",
    "normalize_timestamp",
    "normalize_decision",
    "normalize_transition",
]

from .content import (
    primary_text,
    primary_text_and_field,
)

__all__ += [
    "primary_text",
    "primary_text_and_field",
]
