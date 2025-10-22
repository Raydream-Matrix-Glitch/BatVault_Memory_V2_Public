import json
from typing import Dict, Any

import pytest

from services.memory_api.src.memory_api.policy import (
    field_mask,
    field_mask_with_summary,
    acl_check,
    policy_fingerprint,
)


def _base_policy(extra_visible=None) -> Dict[str, Any]:
    # Minimal effective policy surface used by Memory for masking and ACL.
    # Keep structure aligned with role_profile shape used in production code.
    role_profile = {
        "field_visibility": {
            "decision": {
                "visible_fields": ["id", "type", "domain", "title", "description", "timestamp", "decision_maker"],
                "rationale_visible": True,
            }
        },
    }
    return {
        "role": "engineer",
        "namespaces": ["eng"],
        "domain_scopes": ["eng/*"],
        "sensitivity_ceiling": "internal",
        "role_profile": role_profile,
        "extra_visible": list(extra_visible or []),
        "policy_fp": policy_fingerprint(
            {
                "role": "engineer",
                "domain_scopes": ["eng/*"],
                "sensitivity_ceiling": "internal",
                "extra_visible": list(extra_visible or []),
            }
        ),
    }


def test_xextra_dot_path_allowlist_nested_copy():
    node = {
        "id": "eng#d-1",
        "type": "DECISION",
        "domain": "eng",
        "title": "Ship A",
        "description": "desc",
        "timestamp": "2025-09-01T10:00:00Z",
        "decision_maker": {"id": "org:vp-eng", "role": "VP"},
        "x-extra": {"okr": "OKR-1", "meta": {"prio": "high", "tags": ["x", "y"]}, "ignore": 1},
    }
    policy = _base_policy(extra_visible=["okr", "meta.prio"])
    masked = field_mask(node, policy)
    # x-extra keys restricted to allow-list
    assert set(masked.get("x-extra", {}).keys()) == {"okr", "meta"}
    assert masked["x-extra"]["meta"] == {"prio": "high"}  # dot-path took only the subtree
    # unchanged base fields remain
    assert masked["title"] == "Ship A"


def test_acl_allows_within_scope_and_ceiling():
    node = {
        "id": "eng#d-1",
        "type": "DECISION",
        "domain": "eng/product",
        "timestamp": "2025-09-01T10:00:00Z",
        "sensitivity": "internal",
    }
    policy = _base_policy()
    allowed, reason = acl_check(node, policy)
    assert allowed, f"unexpected deny: {reason}"


def test_acl_denies_out_of_scope_domain():
    node = {
        "id": "hr#d-1",
        "type": "DECISION",
        "domain": "hr",
        "timestamp": "2025-09-01T10:00:00Z",
        "sensitivity": "low",
    }
    policy = _base_policy()
    allowed, reason = acl_check(node, policy)
    assert not allowed and reason == "acl:domain_out_of_scope"


def test_policy_fingerprint_stable_over_key_order():
    p1 = _base_policy(extra_visible=["okr", "meta.prio"])
    p2 = _base_policy(extra_visible=["meta.prio", "okr"])  # different order
    assert p1["policy_fp"] == p2["policy_fp"], "policy_fp must be order-insensitive"


def test_field_mask_with_summary_reports_denied_fields_without_values():
    node = {
        "id": "eng#d-1",
        "type": "DECISION",
        "domain": "eng",
        "timestamp": "2025-09-01T10:00:00Z",
        "title": "T",
        "description": "D",
        "secret_field": "should_be_removed",
        "x-extra": {"ok": 1},
    }
    policy = _base_policy(extra_visible=["*"])
    masked, summary = field_mask_with_summary(node, policy)
    assert "secret_field" not in masked
    removed = {d["field"] for d in summary.get("removed", [])}
    assert "secret_field" in removed
    # No sensitive values are present in the summary
    assert all("value" not in d for d in summary.get("removed", []))