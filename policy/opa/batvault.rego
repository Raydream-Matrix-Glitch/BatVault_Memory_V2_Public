package batvault

import future.keywords.contains
import future.keywords.if

default denied_status := 0

# Allowed IDs = { anchor } ∪ { all edge endpoints } (deterministic, sorted).
allowed_ids := ids {
  anchor := input.anchor_id
  edges := input.edges
  s_anchor := {anchor}
  s_from   := {e.from | e := edges[_]}
  s_to     := {e.to   | e := edges[_]}
  s := s_anchor | s_from | s_to
  ids := sort(s)
}

# Extra-visible x-extra keys (engine-controlled; can be empty).
extra_visible := ev {
  not input.headers["x-extra-allow"]
  ev := []
} else := ev {
  ev := split(input.headers["x-extra-allow"], ",")
}

# Policy fingerprint: stable hash of the decision projection.
policy_fingerprint := fp {
  proj := {"allowed_ids": allowed_ids, "extra_visible": extra_visible}
  bytes := json.marshal(proj)
  b64 := crypto.sha256(bytes)
  fp := sprintf("sha256:%s", [b64])
}