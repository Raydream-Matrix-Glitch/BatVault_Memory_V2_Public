"""
Domain normalisation helpers for BatVault.

Single source of truth for domain + anchor parsing/validation.
This module defines a single function ``normalise_domain`` which takes an
arbitrary input string and returns a canonical slash‑scoped, lower‑kebab
domain.  The canonical form adheres to the following rules:

* Unicode inputs are normalised using NFKC and lower‑cased.
* Underscores and whitespace characters are converted to single dashes (``-``).
* Duplicate dashes are collapsed; leading and trailing dashes on each segment are removed.
* Domains consist of one or more segments separated by ``/``.  Each segment must match
  the pattern ``^[a-z0-9]+(?:-[a-z0-9]+)*$``.
* Normalisation rejects empty segments (e.g. ``"/foo"`` or "foo//bar") and any segment
  containing invalid characters.

If the input cannot be normalised into a valid domain, a ``ValueError`` is raised.
Callers may catch this to return a structured error or reject the write as per the ingest specification.
"""

from __future__ import annotations
import re
import unicodedata
from typing import Union, Tuple


_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def normalise_domain(domain: Union[str, bytes]) -> str:
    """Return a canonical lower‑kebab, slash‑scoped domain.

    Parameters
    ----------
    domain : str | bytes
        The input domain to normalise.  If ``bytes``, it will be decoded as UTF‑8.

    Returns
    -------
    str
        The normalised domain.

    Raises
    ------
    ValueError
        If the domain cannot be normalised into a valid lower‑kebab representation.
    """
    if domain is None:
        raise ValueError("domain must be a non-empty string")
    if isinstance(domain, bytes):
        try:
            domain_str = domain.decode("utf-8")
        except Exception:
            raise ValueError(f"domain bytes could not be decoded: {domain!r}")
    else:
        domain_str = str(domain)
    if not domain_str:
        raise ValueError("domain must be a non-empty string")
    norm = unicodedata.normalize("NFKC", domain_str).lower()
    norm = re.sub(r"[_\\s]+", "-", norm)
    parts = norm.split("/")
    normalised_parts: list[str] = []
    for seg in parts:
        seg = re.sub(r"-+", "-", seg).strip("-")
        if not seg:
            raise ValueError(f"invalid domain segment in '{domain_str}'")
        if not _SEGMENT_RE.match(seg):
            raise ValueError(f"invalid domain segment '{seg}' in '{domain_str}'")
        normalised_parts.append(seg)
    return "/".join(normalised_parts)

# Anchor helpers (normative in v3):
# Anchor shape: "<domain>#<id>" where domain is slash-scoped lower-kebab
# (segments matching _SEGMENT_RE) and id matches [a-z0-9._:-]+.  Baseline §0.  :contentReference[oaicite:3]{index=3}
#
_SEGMENT_PATTERN = r"(?:[a-z0-9]+(?:-[a-z0-9]+)*)"
_DOMAIN_PATTERN = rf"{_SEGMENT_PATTERN}(?:/{_SEGMENT_PATTERN})*"
_ANCHOR_RE = re.compile(rf"^({_DOMAIN_PATTERN})#([a-z0-9._:-]+)$")

def is_valid_anchor(anchor: str) -> bool:
    """Return True iff *anchor* matches '<domain>#<id>' (lowercase)."""
    return bool(_ANCHOR_RE.match(anchor or ""))

def is_anchor(anchor: str) -> bool:
    """Alias preferred by services."""
    return is_valid_anchor(anchor)

def parse_anchor(anchor: str) -> Tuple[str, str]:
    """
    Parse '<domain>#<id>' into (domain, id) or raise ValueError with an
    actionable message (fail-closed; no silent coercions).  :contentReference[oaicite:4]{index=4}
    """
    m = _ANCHOR_RE.match(anchor or "")
    if not m:
        raise ValueError(
            f"invalid anchor format: '{anchor}' — expected '<domain>#<id>' (lowercase)"
        )
    return m.group(1), m.group(2)

def anchor_to_storage_key(anchor: str) -> str:
    """
    Map '<domain>#<id>' → storage _key. Arango forbids '#'.
    Deterministic and minimal: '#' → '_'. Keep this at adapter boundaries.
    """
    if not is_valid_anchor(anchor):
        raise ValueError(f"invalid anchor: {anchor!r}")
    return anchor.replace("#", "_")

def storage_key_to_anchor(key: str) -> str:
    """
    Map storage _key back to wire anchor '<domain>#<id>'.
    Inverse of `anchor_to_storage_key` for adapter boundaries.
    Only the delimiter between domain and id is converted:
    the FIRST '_' after the last '/' → '#'.
    """
    if not isinstance(key, str) or not key:
        raise ValueError(f"invalid storage key: {key!r}")
    if "#" in key:
        return key  # already wire-form
    slash = key.rfind('/')
    us = key.find('_', slash + 1)
    if us == -1:
        return key
    return key[:us] + "#" + key[us+1:]

__all__ = ["normalise_domain", "is_valid_anchor", "is_anchor", "parse_anchor",
           "anchor_to_storage_key", "storage_key_to_anchor"]