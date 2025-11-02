package batvault

# ─────────────────────────────────────────────────────────────────────────────
# Roles live in OPA data (e.g., policy/opa/roles.json) as:
# {
#   "roles": {
#     "anonymous": { "field_visibility": {...}, "extra_visible": [] },
#     "analyst":   { "field_visibility": {...}, "extra_visible": [] },
#     "manager":   { "field_visibility": {...}, "extra_visible": ["*"] },
#     "ceo":       { "field_visibility": {...}, "extra_visible": ["*"] }
#   }
# }
# ─────────────────────────────────────────────────────────────────────────────

default role := "anonymous"

# Pick the first recognized role from input.identity.roles (case-insensitive).
role := r {
  some i
  r := lower(input.identity.roles[i])
  data.roles[r]
}

# Selected role config; fail-closed to an empty, restrictive config if missing.
role_cfg := cfg {
  cfg := data.roles[role]
} else := cfg {
  cfg := {
    "field_visibility": {},
    "extra_visible": []
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Decision: allowed_ids, extra_visible, explain, policy_fp
# ─────────────────────────────────────────────────────────────────────────────

# Allowed IDs = { anchor } ∪ { all edge endpoints } (deterministic, sorted).
allowed_ids := ids {
  anchor := input.resource.anchor_id                             # required by the new envelope
  edges  := input.edges                                           # adapter always sends an array (possibly empty)

  s_anchor := {anchor}
  s_from   := {e.from | e := edges[_]}
  s_to     := {e.to   | e := edges[_]}

  s := s_anchor | s_from | s_to
  ids := sort(s)
}

# Extra-visible keys for x-extra come solely from the role (no header fallback).
extra_visible := role_cfg.extra_visible

# Explain surface carries field-masking config back to Memory.
explain := {
  "field_visibility": role_cfg.field_visibility
}

# Policy fingerprint: stable sha256 over the deterministic projection.
# NOTE: returns "sha256:<64-hex>" to match the schema.
policy_fp := fp {
  proj := {"allowed_ids": allowed_ids, "extra_visible": extra_visible}
  bytes := json.marshal(proj)
  sum := crypto.sha256(bytes)
  sum_hex := hex.encode(sum)
  fp := sprintf("sha256:%s", [sum_hex])
}

# Optional explicit object if your client queries /v1/data/batvault/decision.
decision := {
  "allowed_ids":  allowed_ids,
  "extra_visible": extra_visible,
  "explain":       explain,
  "policy_fp":     policy_fp
}
