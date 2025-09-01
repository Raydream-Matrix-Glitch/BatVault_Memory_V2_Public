"""Why‑Decision contract enforcement and validation.

This module implements a permissive validator for the ``WhyDecisionResponse``
schema.  Unlike the previous implementation that merely checked a handful
of invariants, the new validator actively normalises and repairs bundles
according to the contract described in the milestone‑4 spec.  The rules
implemented here cover:

* **Allowed ID union** – the ``allowed_ids`` array on the evidence must
  exactly equal the set formed by the anchor id, every event id, and
  all transition ids.  Ordering is canonical: anchor first, followed
  by events (ascending by timestamp) and then transitions (ascending
  by timestamp).  Any deviation from this union is corrected and a
  structured error is emitted.

* **Supporting ID rules** – every response must cite the anchor and all
  transitions.  Unsupported ids are discarded and duplicates removed
  while preserving relative order.  The anchor is always placed first.
  If the environment variable ``CITE_ALL_IDS`` is set to a truthy value
  then the supporting ids are forced to be equal to the canonical
  ``allowed_ids`` (with the anchor first).

* **Events‑only evidence** – the ``evidence.events`` array may only
  contain atomic events (type ``"event"``).  Any item missing a type
  or having a type other than ``"event"`` is removed and an error is
  recorded.

* **Schema hygiene** – events, transitions and the anchor are scrubbed of
  unknown keys, guaranteed to contain a ``"type"`` field, and have
  ``"x-extra"`` created if absent.  Tags on events are normalised by
  converting to lower case, collapsing non‑alphanumeric characters to
  underscores, deduplicating, and preserving order.  ISO timestamps are
  enforced on events, transitions and the anchor.  Any normalisation
  that alters the original shape is accompanied by a structured error.

* **Structured error model** – instead of opaque strings the validator
  returns a list of dictionaries.  Each dictionary contains a unique
  ``code`` and optional ``details`` describing what was changed.  This
  machine‑readable format enables the gateway to surface meaningful
  diagnostics in the ``meta.validator_errors`` field and decide when
  fallback logic should be invoked.

Because the validator both repairs and annotates the response, callers
should not depend solely on the boolean return value.  A return value
of ``True`` only indicates that the bundle is free from fatal errors
after normalisation.  The presence of any error entries signals that
data has been corrected and the caller may wish to mark the response
as a fallback or degraded result.
"""

import os
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as dateutil_parser

from core_logging import get_logger
from core_models.models import (
    WhyDecisionAnswer,
    WhyDecisionAnchor,
    WhyDecisionEvidence,
    WhyDecisionResponse,
    WhyDecisionTransitions,
    CompletenessFlags,
)
from core_utils import slugify_tag

logger = get_logger("core_validator")

__all__ = ["validate_response"]


def _is_truthy(value: Optional[str]) -> bool:
    """Return True if the supplied environment string represents a truthy value."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ensure_iso(timestamp: str) -> Tuple[str, bool]:
    """Return an ISO‑8601 normalised timestamp and a flag indicating whether it was altered."""
    changed = False
    iso = timestamp
    try:
        dt = dateutil_parser.isoparse(timestamp)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        iso_norm = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if iso_norm != timestamp:
            changed = True
            iso = iso_norm
    except Exception:
        changed = False
        iso = timestamp
    return iso, changed


def _strip_unknown_keys(obj: Dict[str, Any], allowed_keys: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Remove keys from ``obj`` that are not present in ``allowed_keys``."""
    removed: List[str] = []
    cleaned: Dict[str, Any] = {}
    for k, v in obj.items():
        if k in allowed_keys:
            cleaned[k] = v
        else:
            removed.append(k)
    return cleaned, removed


def _normalise_event(event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Normalise a single event dictionary.

    Only atomic events are allowed in the ``evidence.events`` array.  An atomic
    event must declare its ``type`` as ``"event"``.  Items explicitly
    declaring another ``type`` are discarded and recorded with an
    ``events_contains_non_event`` error.  When the ``type`` is absent the
    validator differentiates between bare identifiers and untyped objects
    containing content fields.  Untyped objects that include any of the event
    content fields (summary, description, timestamp, snippet or led_to) are
    considered non‑events, removed from the bundle and flagged with the same
    error.  Bare identifiers (objects with only an ``id``) are assumed to be
    events and are normalised accordingly.

    Unknown keys on valid events are stripped; missing or mismatched
    ``type`` values are coerced to ``"event"`` silently.  Tags are
    slugified (lower‑case ASCII with underscores) and deduplicated while
    preserving order.  An ``x-extra`` mapping is always created when
    absent.  Timestamps are normalised to canonical ISO‑8601 Z format when
    possible; timestamp repairs do not emit errors.

    Returns ``(None, errors)`` when the input is discarded, otherwise a
    cleaned event and any emitted errors.
    """
    errors: List[Dict[str, Any]] = []
    declared_type: str = ""
    if isinstance(event, dict):
        raw_type = event.get("type") or event.get("entity_type") or ""
        declared_type = str(raw_type).lower()
    # Explicitly declared non‑events → drop
    if declared_type:
        if declared_type != "event":
            eid = event.get("id") if isinstance(event, dict) else None
            errors.append({"code": "events_contains_non_event", "details": {"id": eid}})
            return None, errors
    else:
        # No type provided.  If any content field is present this is treated
        # as a non‑event and removed.
        if isinstance(event, dict):
            content_keys = {"summary", "description", "timestamp", "snippet", "led_to"}
            for k in content_keys:
                if k in event and event.get(k) is not None:
                    eid = event.get("id")
                    errors.append({"code": "events_contains_non_event", "details": {"id": eid}})
                    return None, errors
    # From here on we treat the object as an event and normalise it
    allowed_keys = [
        "id",
        "summary",
        "description",
        "timestamp",
        "tags",
        "snippet",
        "led_to",
        "x-extra",
        "type",
    ]
    cleaned, removed_keys = _strip_unknown_keys(event, allowed_keys)
    if removed_keys:
        errors.append({"code": "unknown_event_keys_stripped", "details": {"id": event.get("id"), "removed_keys": removed_keys}})
    # Coerce missing or mismatched type to 'event'
    if cleaned.get("type") != "event":
        cleaned["type"] = "event"
    # Ensure x-extra exists
    if "x-extra" not in cleaned or cleaned.get("x-extra") is None:
        cleaned["x-extra"] = {}
    # Normalise tags
    raw_tags = cleaned.get("tags")
    if raw_tags is None:
        raw_tags_list: List[str] = []
    elif isinstance(raw_tags, (list, tuple)):
        raw_tags_list = list(raw_tags)
    else:
        raw_tags_list = [raw_tags]
    normalised_tags: List[str] = []
    seen_tags: set[str] = set()
    for tag in raw_tags_list:
        try:
            norm = slugify_tag(str(tag))
        except Exception:
            norm = slugify_tag(f"{tag}")
        if norm and norm not in seen_tags:
            normalised_tags.append(norm)
            seen_tags.add(norm)
    if cleaned.get("tags") != normalised_tags:
        cleaned["tags"] = normalised_tags
    # Normalise timestamp
    ts = cleaned.get("timestamp")
    if ts:
        norm_ts, changed = _ensure_iso(ts)
        if changed:
            cleaned["timestamp"] = norm_ts
    return cleaned, errors

def _normalise_transition(tr: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Normalise a single transition dictionary (edge connecting decisions).

    Transitions are *edges* (`from`/`to`/`relation`/`timestamp`). This helper
    strips unknown attributes, coerces the ``type`` to ``"transition"``,
    ensures an ``x-extra`` mapping exists and normalises the ``timestamp``
    to ISO-8601 Z format. Any unknown keys that are removed and any timestamp
    repairs are reported via the structured error list. The returned tuple
    contains the cleaned transition and any emitted errors.
    """
    errors: List[Dict[str, Any]] = []
    allowed_keys = [
        "id",
        "from",
        "to",
        "from_title",
        "to_title",
        "title",
        "relation",
        "reason",
        "timestamp",
        "x-extra",
        "type",
        "entity_type",
    ]
    cleaned, removed_keys = _strip_unknown_keys(tr, allowed_keys)
    if removed_keys:
        errors.append({"code": "unknown_transition_keys_stripped", "details": {"id": tr.get("id"), "removed_keys": removed_keys}})
    declared_type = (cleaned.get("type") or cleaned.get("entity_type") or "").lower()
    # Coerce to type "transition" silently
    if declared_type != "transition":
        cleaned["type"] = "transition"
    # Ensure x-extra exists
    if "x-extra" not in cleaned or cleaned.get("x-extra") is None:
        cleaned["x-extra"] = {}
    ts = cleaned.get("timestamp")
    if ts:
        norm_ts, changed = _ensure_iso(ts)
        if changed:
            cleaned["timestamp"] = norm_ts
            errors.append({"code": "timestamp_normalised", "details": {"id": tr.get("id"), "original": ts, "normalised": norm_ts}})
    return cleaned, errors


def _normalise_anchor(anchor: WhyDecisionAnchor) -> Tuple[WhyDecisionAnchor, List[Dict[str, Any]]]:
    """
    Normalise the anchor object (decision).

    This helper scrubs unknown keys, normalises tags and timestamps, and
    validates the result against the ``WhyDecisionAnchor`` schema.  The
    anchor model forbids unknown fields (``extra='forbid'``), so we
    deliberately avoid attaching transport‑only keys such as ``type`` or
    ``x-extra`` here.  Adding those keys would cause ``model_validate``
    to fail and force the validator to drop important fields when falling
    back to a minimal anchor.  Upstream services remain responsible for
    adding ``type`` and ``x-extra`` to transport envelopes when required.
    """
    errors: List[Dict[str, Any]] = []
    data = anchor.model_dump(mode="python") if isinstance(anchor, WhyDecisionAnchor) else dict(anchor)
    # Keep only fields defined on WhyDecisionAnchor.
    allowed_keys = list(WhyDecisionAnchor.model_fields.keys())
    cleaned, removed_keys = _strip_unknown_keys(data, allowed_keys)
    if removed_keys:
        errors.append({"code": "unknown_anchor_keys_stripped",
                       "details": {"id": data.get("id"), "removed_keys": removed_keys}})
    # Normalise tags and timestamp; emit errors only when a change is made.
    raw_tags = cleaned.get("tags")
    if raw_tags is None:
        raw_tags_list: List[str] = []
    elif isinstance(raw_tags, (list, tuple)):
        raw_tags_list = list(raw_tags)
    else:
        raw_tags_list = [raw_tags]
    normalised_tags: List[str] = []
    seen_tags: set[str] = set()
    for tag in raw_tags_list:
        try:
            norm = slugify_tag(str(tag))
        except Exception:
            norm = slugify_tag(f"{tag}")
        if norm and norm not in seen_tags:
            normalised_tags.append(norm)
            seen_tags.add(norm)
    if cleaned.get("tags") != normalised_tags:
        errors.append({
            "code": "tags_normalised",
            "details": {
                "id": cleaned.get("id"),
                "original": raw_tags_list,
                "normalised": normalised_tags,
            },
        })
        cleaned["tags"] = normalised_tags
    ts = cleaned.get("timestamp")
    if ts:
        norm_ts, changed = _ensure_iso(ts)
        if changed:
            errors.append({
                "code": "timestamp_normalised",
                "details": {
                    "id": cleaned.get("id"),
                    "original": ts,
                    "normalised": norm_ts,
                },
            })
            cleaned["timestamp"] = norm_ts
    # Validate the cleaned anchor.  Because we’ve removed unknown keys, this
    # should succeed.  If it does fail (e.g. due to type mismatch), build a
    # fallback anchor retaining all cleaned fields rather than just id/title.
    try:
        new_anchor = WhyDecisionAnchor.model_validate(cleaned)
    except Exception:
        new_anchor = WhyDecisionAnchor(
            id=cleaned.get("id"),
            title=cleaned.get("title") or cleaned.get("option"),
            option=cleaned.get("option"),
            rationale=cleaned.get("rationale"),
            timestamp=cleaned.get("timestamp"),
            decision_maker=cleaned.get("decision_maker"),
            tags=cleaned.get("tags") or [],
            supported_by=cleaned.get("supported_by") or [],
            based_on=cleaned.get("based_on") or [],
            transitions=cleaned.get("transitions") or [],
        )
    return new_anchor, errors


def _canonical_allowed_ids(anchor_id: str, events: List[Dict[str, Any]], transitions: List[Dict[str, Any]]) -> List[str]:
    """Compute the canonical allowed ids list."""
    ids: List[str] = []
    seen: set[str] = set()
    if anchor_id:
        ids.append(anchor_id)
        seen.add(anchor_id)
    sorted_events = sorted(events, key=lambda e: e.get("timestamp") or "")
    for e in sorted_events:
        eid = e.get("id")
        if eid and eid not in seen:
            ids.append(eid)
            seen.add(eid)
    sorted_trans = sorted(transitions, key=lambda t: t.get("timestamp") or "")
    for tr in sorted_trans:
        tid = tr.get("id")
        if tid and tid not in seen:
            ids.append(tid)
            seen.add(tid)
    return ids

# ---------------------------------------------------------------------------
# Public helper for canonical allowed ids
# ---------------------------------------------------------------------------

def canonical_allowed_ids(anchor_id: str, events: List[Dict[str, Any]], transitions: List[Dict[str, Any]]) -> List[str]:
    """
    Compute the canonical list of allowed identifiers for a WhyDecision bundle.

    This public wrapper delegates to the internal ``_canonical_allowed_ids``
    function to produce a stable ordering across the anchor, events and
    transitions.  The resulting list always begins with the anchor ID (if
    provided), followed by event IDs in ascending timestamp order, and then
    transition IDs in ascending timestamp order.  Duplicate IDs are removed
    while preserving the first occurrence.
    """
    return _canonical_allowed_ids(anchor_id, events, transitions)

def validate_response(resp: Any) -> Tuple[bool, List[Dict[str, Any]]]:
    """Validate and normalise a ``WhyDecisionResponse``.  Returns (ok, errors)."""
    try:
        model_resp: WhyDecisionResponse = (
            resp
            if isinstance(resp, WhyDecisionResponse)
            else WhyDecisionResponse.model_validate(resp)
        )
    except Exception as exc:
        return False, [{"code": "invalid_response", "details": {"error": str(exc)}}]
    errors: List[Dict[str, Any]] = []

    anchor, anchor_errors = _normalise_anchor(model_resp.evidence.anchor)
    if anchor_errors:
        errors.extend(anchor_errors)
    model_resp.evidence.anchor = anchor
    anchor_id = anchor.id

    new_events: List[Dict[str, Any]] = []
    invalid_event_count = 0
    for e in list(model_resp.evidence.events or []):
        try:
            e_dict = e if isinstance(e, dict) else e.model_dump(mode="python")
        except Exception:
            e_dict = dict(e)
        cleaned, ev_errors = _normalise_event(e_dict)
        if cleaned is not None:
            new_events.append(cleaned)
        else:
            # Track how many events were discarded as invalid for completeness checks
            invalid_event_count += 1
        if ev_errors:
            errors.extend(ev_errors)
    model_resp.evidence.events = new_events

    new_preceding: List[Dict[str, Any]] = []
    for tr in list(model_resp.evidence.transitions.preceding or []):
        tr_dict = tr if isinstance(tr, dict) else tr.model_dump(mode="python")
        cleaned, tr_errors = _normalise_transition(tr_dict)
        new_preceding.append(cleaned)
        if tr_errors:
            errors.extend(tr_errors)
    new_succeeding: List[Dict[str, Any]] = []
    for tr in list(model_resp.evidence.transitions.succeeding or []):
        tr_dict = tr if isinstance(tr, dict) else tr.model_dump(mode="python")
        cleaned, tr_errors = _normalise_transition(tr_dict)
        new_succeeding.append(cleaned)
        if tr_errors:
            errors.extend(tr_errors)
    model_resp.evidence.transitions = WhyDecisionTransitions(
        preceding=new_preceding,
        succeeding=new_succeeding,
    )

    expected_allowed = _canonical_allowed_ids(
        anchor_id,
        new_events,
        new_preceding + new_succeeding,
    )
    orig_allowed = list(model_resp.evidence.allowed_ids or [])
    orig_set = set(orig_allowed)
    expected_set = set(expected_allowed)
    if orig_set != expected_set:
        removed = [x for x in orig_allowed if x not in expected_set]
        added = [x for x in expected_allowed if x not in orig_set]
        errors.append({
            "code": "allowed_ids_exact_union_violation",
            "details": {"removed": removed, "added": added},
        })
        model_resp.evidence.allowed_ids = expected_allowed
    else:
        # When sets are equal but ordering differs, silently reorder to the canonical order.
        if orig_allowed != expected_allowed:
            model_resp.evidence.allowed_ids = expected_allowed

    supp_ids: List[str] = []
    try:
        supp_ids = list(model_resp.answer.supporting_ids or [])
    except Exception:
        supp_ids = []
    if anchor_id:
        if anchor_id not in supp_ids:
            errors.append({"code": "supporting_ids_missing_anchor", "details": {"anchor_id": anchor_id}})
        if not supp_ids or supp_ids[0] != anchor_id:
            if anchor_id in supp_ids:
                supp_ids = [x for x in supp_ids if x != anchor_id]
            supp_ids.insert(0, anchor_id)
    transition_ids = [tr.get("id") for tr in (new_preceding + new_succeeding) if tr.get("id")]
    missing_transitions = [tid for tid in transition_ids if tid not in supp_ids]
    if missing_transitions:
        for tid in transition_ids:
            if tid not in supp_ids:
                supp_ids.append(tid)
        errors.append({"code": "supporting_ids_missing_transition", "details": {"missing": missing_transitions}})
    filtered_supp = []
    allowed_set = set(model_resp.evidence.allowed_ids or [])
    removed_support: List[str] = []
    # Remove duplicates and filter out ids not in allowed_set.  Collect removed ids
    for sid in supp_ids:
        if sid in allowed_set and sid not in filtered_supp:
            filtered_supp.append(sid)
        elif sid not in allowed_set:
            removed_support.append(sid)
    # Determine whether CITE_ALL_IDS is enabled up front
    cite_all_env = os.getenv("CITE_ALL_IDS")
    cite_all = _is_truthy(cite_all_env)
    # Emit removed-invalid error only when not citing all ids
    if removed_support and not cite_all:
        errors.append({"code": "supporting_ids_removed_invalid", "details": {"removed": removed_support}})
    # Update supporting_ids to the filtered list
    supp_ids = filtered_supp
    if cite_all:
        # In cite-all mode supporting_ids must match allowed_ids exactly.  Only emit
        # an error when the existing order deviates from the canonical allowed_ids.
        if supp_ids != model_resp.evidence.allowed_ids:
            errors.append({"code": "supporting_ids_enforced_cite_all_ids", "details": {"before": supp_ids, "after": model_resp.evidence.allowed_ids}})
        supp_ids = list(model_resp.evidence.allowed_ids)
    model_resp.answer.supporting_ids = supp_ids

    flags = model_resp.completeness_flags
    # Update preceding/succeeding flags silently; mismatches do not produce errors
    has_pre = bool(new_preceding)
    has_suc = bool(new_succeeding)
    if flags.has_preceding != has_pre:
        flags.has_preceding = has_pre
    if flags.has_succeeding != has_suc:
        flags.has_succeeding = has_suc
    valid_event_count = len(new_events)
    # Determine if an event_count mismatch should be recorded.  A mismatch is recorded
    # when either (a) at least one event was removed as invalid, or (b) no transitions are
    # present and the provided flag does not match the number of valid events.  When
    # transitions exist and all events were accepted, the flag is updated silently.
    event_count_mismatch = False
    if invalid_event_count > 0:
        event_count_mismatch = True
    elif not new_preceding and not new_succeeding and flags.event_count != valid_event_count:
        event_count_mismatch = True
    if event_count_mismatch:
        errors.append({"code": "completeness_event_count_mismatch", "details": {"expected": flags.event_count, "actual": valid_event_count}})
    if flags.event_count != valid_event_count:
        flags.event_count = valid_event_count

    # Determine fatality.  A response is fatal if we encountered an
    # invalid_response error from the schema validator or if the only
    # semantic violation is that supporting_ids contained an invalid id
    # without any accompanying missing-anchor or missing-transition errors.
    fatal = any(err.get("code") == "invalid_response" for err in errors)
    if not fatal:
        codes = {err.get("code") for err in errors if isinstance(err, dict)}
        if "supporting_ids_removed_invalid" in codes:
            # If this is the only support-related error (no missing anchor or transitions),
            # treat it as fatal.  Otherwise it is repairable and non-fatal.
            if "supporting_ids_missing_anchor" not in codes and "supporting_ids_missing_transition" not in codes:
                fatal = True
    ok = not fatal
    if not isinstance(resp, WhyDecisionResponse):
        try:
            resp.clear()
            resp.update(model_resp.model_dump(mode="python"))
        except Exception:
            pass
    return ok, errors