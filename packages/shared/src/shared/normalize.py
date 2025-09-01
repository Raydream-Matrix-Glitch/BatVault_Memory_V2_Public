"""
Canonical normalisation routines for BatVault node payloads.

This module defines helpers to normalise incoming documents prior to
storage or external presentation.  The functions enforce a stable
shape across decision, event and transition node types.  They are
pure and operate on shallow copies of the input dictionaries.

Normalisation rules (authoritative):

* Guarantee that ``x-extra`` exists and is a dictionary.  When the
  field is missing or is not a mapping, it will be replaced with an
  empty object.
* Normalise tag values using ``core_utils.slugify_tag``:
  lowercase, ASCII-only, non-alphanumerics collapsed to single underscores,
  deduplicated with order preserved. This guarantees the same tag shape
  across ingest, Memory API, and Gateway.
* Whitelist allowed attributes per node type.  Unknown keys (for
  example ``edge`` or ``title``) are silently dropped.  Common
  transport keys such as ``snapshot_etag`` and ``meta`` are always
  preserved.
* Assign a ``type`` field when it is absent.  The provided node
  context (``event``, ``decision`` or ``transition``) is used to
  populate the field.  Downstream consumers rely on this field to
  determine the document category.
* Enforce ISO‑8601 timestamps.  When the timestamp string parses as
  a recognised datetime value it is round‑tripped into a canonical
  form: all datetimes are converted to UTC and rendered as
  ``YYYY‑MM‑DDTHH:MM:SSZ``.  When a timestamp is malformed a
  ``ValueError`` is raised.  If the value already conforms to
  ISO‑8601 with a timezone the original string is retained.

These routines are intended to be used at ingestion time prior to
inserting records into the underlying store.  They may also be
applied defensively in API layers where unnormalised objects may
appear (for example in unit tests).  However, after ingestion,
documents fetched from the store should already be normalised and
should not need further processing.
"""

from datetime import timezone
import re
from typing import Any, Dict, List

from core_utils import slugify_tag

from dateutil import parser as dtp

__all__ = [
    "normalize_event",
    "normalize_decision",
    "normalize_transition",
    "mirror_option_to_title",
    "mirror_title_to_option",
]


def _ensure_x_extra(doc: Dict[str, Any]) -> None:
    """Ensure that the ``x-extra`` key exists and is a dict."""
    extra = doc.get("x-extra")
    if not isinstance(extra, dict):
        doc["x-extra"] = {}


def _normalize_tags(tags) -> Any:
    """
    Normalise tags by lower‑casing and collapsing non‑alphanumeric characters to
    underscores.  Duplicate tags are removed while preserving the order
    in which they first appear.  When ``tags`` is not a list, the value is
    returned unchanged.  When the input is ``None`` or an empty list, this
    helper returns an empty list (not ``None``) to ensure that all normalised
    objects present a consistent JSON shape.

    Parameters
    ----------
    tags
        A list of tags or any other type.  If not a list, the input
        is returned unchanged.

    Returns
    -------
    list[str] | Any
        A list of normalised, unique tags in original order, or
        ``None`` for empty inputs.  Non‑list inputs are returned
        unchanged.
    """
    # Non‑list inputs are passed through unchanged
    if not isinstance(tags, list):
        return tags
    # Treat missing or empty tag lists as an empty list instead of None.  This
    # aligns with downstream validators and Pydantic models which always
    # materialise tags as a list.  Returning None for empty inputs led to
    # inconsistent JSON shapes across services.
    if not tags:
        return []
    normalised: List[str] = []
    seen: set[str] = set()
    for t in tags:
        try:
            slug = slugify_tag(str(t))
        except Exception:
            slug = slugify_tag(f"{t}")
        if slug and slug not in seen:
            normalised.append(slug)
            seen.add(slug)
    # If no tags remain after normalisation return an empty list rather than None.
    return normalised if normalised else []


def _norm_timestamp(ts: Any) -> Any:
    """Coerce a timestamp into a canonical ISO‑8601 string.

    When ``ts`` is a string, attempt to parse it using dateutil.  If no
    timezone is present, assume UTC.  The returned value is rendered
    as ``YYYY‑MM‑DDTHH:MM:SSZ``.  When parsing fails a ValueError is
    propagated.  Non‑string values are returned unchanged.
    """
    if not isinstance(ts, str):
        return ts
    try:
        dt = dtp.parse(ts)
    except Exception as exc:
        raise ValueError(f"Invalid timestamp '{ts}': {exc}") from exc
    # If no timezone info, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Convert to UTC
    dt = dt.astimezone(timezone.utc)
    # Serialise using Z for UTC
    iso = dt.isoformat().replace("+00:00", "Z")
    return iso

def normalize_timestamp(ts: Any) -> str:
    """
    Public wrapper that coerces a timestamp into canonical ISO-8601 Z format.
    Accepts strings; non-strings are converted to str before parsing.
    """
    if not isinstance(ts, str):
        ts = str(ts)
    return _norm_timestamp(ts)


def _filter_fields(doc: Dict[str, Any], allowed: set[str]) -> Dict[str, Any]:
    """Return a new dict containing only allowed keys.

    Keys not present in the ``allowed`` set are dropped.  Note that
    this function is shallow – nested dictionaries are not filtered.
    """
    return {k: v for k, v in doc.items() if k in allowed}

def mirror_title_to_option(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mirror the ``title`` field into ``option`` for decision documents when appropriate.
    Complements ``mirror_option_to_title``. Update is in place.
    """
    if isinstance(doc, dict):
        opt = doc.get("option")
        if (opt is None or (isinstance(opt, str) and not opt.strip())) and isinstance(doc.get("title"), str):
            title_val = doc.get("title")
            if isinstance(title_val, str) and title_val.strip():
                doc["option"] = title_val
    return doc

# ---------------------------------------------------------------------------
# Decision‑specific helpers

def mirror_option_to_title(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mirror the ``option`` field into ``title`` for decision documents when appropriate.

    This helper inspects the provided document and, if it contains a non‑``None``
    ``option`` key but does not define a ``title`` key, it adds a ``title`` entry
    with the same value as ``option``.  The update is performed in place and the
    document is returned for convenience.

    Parameters
    ----------
    doc : dict
        A decision document produced by :func:`normalize_decision`.

    Returns
    -------
    dict
        The document with ``title`` mirrored from ``option`` when necessary.

    Notes
    -----
    If ``title`` is already present (even if its value is falsy), it will not be
    overwritten.  If ``option`` is absent or ``None``, no ``title`` field is added.
    """
    # Only mirror when option exists and title is missing
    if doc is not None and "option" in doc and doc.get("option") is not None and "title" not in doc:
        doc["title"] = doc["option"]
    return doc


def _common_normalise(doc: Dict[str, Any], node_type: str, allowed_keys: set[str]) -> Dict[str, Any]:
    """Perform common normalisation steps for any node type."""
    out = dict(doc or {})
    # Guarantee x-extra exists and is a dict
    _ensure_x_extra(out)
    # Normalise tags (if present)
    if "tags" in out:
        out["tags"] = _normalize_tags(out.get("tags"))
    # Coerce timestamp (if present) to canonical ISO-8601
    if "timestamp" in out:
        try:
            out["timestamp"] = _norm_timestamp(out["timestamp"])
        except ValueError:
            # Re-raise to caller; invalid timestamps are considered fatal
            raise
    # Always enforce the canonical node type
    out["type"] = node_type
    # Allow pass-through keys
    allowed = set(allowed_keys) | {"snapshot_etag", "meta", "x-extra", "type"}
    # Move unknown keys into x-extra (schema-agnostic preservation)
    unknown = {k: v for k, v in out.items() if k not in allowed}
    if unknown:
        xe = out.get("x-extra")
        xe = xe if isinstance(xe, dict) else {}
        xe.update(unknown)
        out["x-extra"] = xe
    # Finally, drop unknowns from top-level after preserving them
    out = _filter_fields(out, allowed)
    return out


def normalize_event(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise an event document.

    Allowed keys for events:
      * id
      * summary
      * description
      * timestamp
      * tags
      * led_to
      * snippet
      * x-extra
      * type (added when missing)
      * snapshot_etag (passed through)
      * meta (passed through)

    Unknown attributes are dropped silently.
    """
    allowed = {
        "id",
        "summary",
        "description",
        "timestamp",
        "tags",
        "led_to",
        "snippet",
        "x-extra",
        "type",
    }
    return _common_normalise(doc, "event", allowed)


def normalize_decision(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a decision document.

    Allowed keys for decisions:
      * id
      * option
      * title (mirrored from ``option`` when not provided)
      * rationale
      * description
      * timestamp
      * decision_maker
      * tags
      * supported_by
      * based_on
      * transitions
      * x-extra
      * type (added when missing)
      * snapshot_etag (passed through)
      * meta (passed through)

    Unknown attributes are dropped silently.

    When the incoming decision includes an ``option`` but lacks a ``title``,
    the returned dictionary will include a ``title`` entry with the same value
    as ``option``.  Existing ``title`` values are preserved and never overwritten.
    """
    allowed = {
        "id",
        "option",
        "title",          # keep mirrored or upstream-provided titles at top-level
        "rationale",
        "description",
        "timestamp",
        "decision_maker",
        "tags",
        "supported_by",
        "based_on",
        "transitions",
        "x-extra",
        "type",
    }
    # Accept either alias at input; prefer canonical ``option``
    doc = mirror_title_to_option(doc)
    out = _common_normalise(doc, "decision", allowed)
    return mirror_option_to_title(out)


def normalize_transition(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a transition document.

    Allowed keys for transitions:
      * id
      * from
      * to
      * from_title
      * to_title
      * title
      * relation
      * reason
      * timestamp
      * tags
      * x-extra
      * type (added when missing)
      * snapshot_etag (passed through)
      * meta (passed through)

    Unknown attributes are dropped silently.
    """
    allowed = {
        "id",
        "from",
        "to",
        "from_title",
        "to_title",
        "title",
        "relation",
        "reason",
        "timestamp",
        "tags",
        "x-extra",
        "type",
    }
    return _common_normalise(doc, "transition", allowed)

# --- Evidence utilities promoted from gateway.evidence ------------------
_CURRENCY_MAP = {
    "$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR",
    "¥": "JPY", "jpy": "JPY",
    "£": "GBP", "gbp": "GBP",
}

def _parse_amount(val: str, unit: str | None) -> float | None:
    try:
        # Remove thin spaces and normal spaces often used as thousands separators
        v = val.replace("\u202f", "").replace("\u00a0", "").replace(" ", "")
        # European style numbers may use a comma as the decimal separator.  When
        # the value contains a single comma and no dot, treat it as a decimal
        # point.  Otherwise strip all commas as thousands separators.
        if "," in v and "." not in v:
            v = v.replace(",", ".")
        # Finally remove any remaining thousands separators
        num = float(v.replace(",", ""))
    except Exception:
        return None
    if not unit:
        return num
    u = unit.lower()
    if u in ("k", "thousand"):
        num *= 1_000.0
    elif u in ("m", "million", "millions"):
        num *= 1_000_000.0
    elif u in ("b", "billion", "billions"):
        num *= 1_000_000_000.0
    return num

def normalise_event_amount(ev: dict) -> None:
    """
    Extract a normalised amount and currency code from an event's textual fields
    (summary/description) and attach them to the event as:
      - normalized_amount: float (base units)
      - normalized_currency: str (ISO-like)
    Safe to call multiple times; leaves ev unchanged if nothing is found.
    """
    if not isinstance(ev, dict) or "normalized_amount" in ev:
        return
    texts: list[str] = []
    for fld in ("summary", "description"):
        val = ev.get(fld)
        if isinstance(val, str):
            texts.append(val)
    import re as _re
    pattern = r"(?i)(?P<currency>[\\$€¥£]|[A-Z]{3})?\\s*([\\d,]+(?:\\.\\d+)?)\\s*(?P<unit>[kmb]|thousand|million|billion|millions|billions)?"
    for txt in texts:
        for m in re.finditer(pattern, txt):
            cur = (m.group("currency") or "").strip()
            unit = m.group("unit")
            val_str = m.group(2)
            cur_key = cur.lower()
            currency = _CURRENCY_MAP.get(cur_key) if cur else None
            amount = _parse_amount(val_str, unit)
            if amount is None:
                continue
            if not currency:
                tail = txt[m.end():]
                m2 = _re.search(r"(?i)\\b([A-Z]{3})\\b", tail)
                if m2:
                    currency = _CURRENCY_MAP.get(m2.group(1).lower(), m2.group(1).upper())
            if amount is not None:
                ev.setdefault("normalized_amount", float(amount))
                if currency:
                    ev.setdefault("normalized_currency", currency)
                return