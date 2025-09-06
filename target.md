IMPORTANT: Test compatibility not important right now!!!!!!!!!!! Clean code implementation and root cause fixes changes with deletion of code is more important. 


Org-Memory: Policies, Alias Projections & Policy-Aware Pre-Selector

(authoritative implementation brief — end-to-end, with meta shape, roles, flows, artifacts, security, and a short roadmap)

0) Why we're doing this (context)

We're building an organizational memory that lets different layers of an org see the same change at the right fidelity, safely and explainably.

Decisions capture what we chose.

Events capture what happened.

Transitions (causal edges) explain why within a domain.

Alias Events let an upstream Decision appear as a local Event in a lower domain (projection).

Policies (role, namespaces, sensitivity) determine:

which vertices you can see (and which fields),

which edge types you can walk (e.g., transitions, aliases),

how far you can walk (k=1 for our demo).

We enforce policy before ranking in a Pre-Selector that runs inside the Memory API (adjacent to Arango). The Gateway remains storage-agnostic: it ranks the already-sanitized set (Selector), clips in order (Budget Gate), and composes a safe answer (Templater).

1) Quick mental model (keep handy)

Alias Event: a normal event document in a lower domain with x-extra.alias_of_decision. It is a safe projection with its own ACL (namespaces, roles, sensitivity).

Alias Edge: a non-causal overlay that links Decision → (Alias) Event so cross-level influence is visible in graphs & audits without polluting causal "why" traversals.

Pre-Selector (Memory API): role-aware graph expansion (edge allowlist, domain scopes, ACL, field masking, k=1) → returns a sanitized CandidateSet + PolicyTrace.

Gateway: Selector (rank visible items once) → Budget Gate (clip in order, no re-rank) → Templater (compose from included items; add permissions note if anything was withheld by policy).

2) Templater envelope (answer shape)

Format:

{decision_maker} on {anchor_date}: {anchor_title}.
Supporting Facts: {event_1.summary} ({event_1.date}); {event_2.summary} ({event_2.date}); {event_3.summary} ({event_3.date})
From: {preceding_decision_id_or_title}. Next: {succeeding_decision_id_or_title}.


Permissions note (append only when policy withheld items):

Note: Some evidence was withheld due to your permissions.

The envelope is small and safe (no enrichment tables); all enrichment lives in the evidence files that are role-masked.

3) Evidence set glossary (authoritative)

evidence_sets.pool_ids → discovery superset (everything found) before budgeting.

evidence_sets.prompt_included_ids → items that entered the prompt.

evidence_sets.prompt_excluded_ids → array of {id, reason} (e.g., "token_budget", "low_weight", "policy_cap").

evidence_sets.payload_included_ids → items returned in the JSON payload.

evidence_sets.payload_excluded_ids → {id, reason} we intentionally withhold (ACL/redaction/snapshot guards).

Distinguish policy-withheld (payload_excluded) from budget-clipped (prompt_excluded).

4) Roles & policies

Role profiles (fixtures) drive enforcement:

Staff (IC)

Domains: acme/region_*

Sensitivity: low

Edges: transitions in region; aliases product→region.

Fields: event summaries/snippets; no corporate rationales.

Manager

Domains: acme/product, acme/region_*

Sensitivity: medium

Edges: transitions product+region; aliases corporate→product and product→region.

Fields: product decisions full; corporate decisions as headers only (title/date), no rationale.

Director/Exec

Domains: acme/*

Sensitivity: high

Edges: all transitions + aliases.

Fields: full documents, whitelisted x-extra (e.g., KPIs).

Headers (policy passport via api_edge → Memory API):

Required:
X-User-Id, X-User-Roles, X-User-Namespaces,
X-Policy-Version, X-Policy-Key, X-Request-Id, X-Trace-Id

Optional:
X-Domain-Scopes, X-Edge-Allow, X-Max-Hops (1), X-Sensitivity-Ceiling
Memory API enforces the lower of role and header ceilings; fail closed if missing.

5) Graph model & fixtures

Vertices: decisions, events (alias events are just events with x-extra.alias_of_decision).

Edges:

transitions (causal) → stored as edges with type="CAUSAL_PRECEDES".

aliases (overlay) → stored as edges with type="ALIAS_OF" from Decision → Alias Event.

Fixtures layout:

memory/fixtures/
  decisions/*.json
  events/*.json
  transitions/*.json         (edges: CAUSAL_PRECEDES)
  edges/aliases/*.json       (edges: ALIAS_OF – projection overlay)


Relation catalog: include "ALIAS_OF" so Memory API can gate by type deterministically.

Ingest order: vertices → transitions → alias overlays.

6) Ingest, storage, normalization (what's there vs. what we add)

Already have

Shared normalizers that preserve x-extra on decisions/events/transitions.

Arango: nodes + edges (named graph), traversal helper returning neighbors with edge.type.

Ingest writes nodes + transition edges.

Memory API returns normalized docs; Gateway uses strict models at prompt surface.

Add

Alias overlay ingest: scan edges/aliases, validate RI (Decision & Event exist), upsert edges with type="ALIAS_OF".

Add "ALIAS_OF" to relation catalog.

(Optional) store timestamp/tags on alias edges for sorting/filters.

Keep x-extra on alias edges if useful for audits; Memory API can choose which subpaths to expose.

No Arango schema change required — alias edges share the edges collection.

7) Where the Pre-Selector lives (and why)

Memory API runs the Pre-Selector (security-first):

Resolve role profile from policy/registry.

Traverse from the anchor with:

edge allowlist (e.g., allow ALIAS_OF only in permitted directions),

domain scopes,

k=1 hop limit (demo).

Apply vertex ACL: roles_allowed ∩ role, namespaces ∩ header namespaces, sensitivity ≤ ceiling.

Apply field masking per role (e.g., manager gets corporate headers only).

Return:

CandidateSet (sanitized nodes & traversed edges with edge.type), and

PolicyTrace (withheld IDs + reasons; counts; edge types used).

Gateway never receives disallowed text; it runs Selector → Budget Gate → Templater on the sanitized set and records audit meta.

8) Selector & confidence signals (beyond date/importance)

Deterministic, CPU-cheap features:

Importance (fixture prior, 0–1)

Recency (days from anchor)

Similarity (title/tags overlap; simple BM25 or token cosine if available)

Type/role boosts (anchor-adjacent, succinct snippet)

Completeness (presence of maker/date/rationale for anchor)

Content quality (length/noise penalty)

Deduping (light Jaccard)

Emit in selection_metrics:
ranking_policy + per-ID scores (e.g., {sim, recency_days, importance}).

9) Budget Gate (no re-rank, just clip in order)

Input: selector's ordered IDs + token budget (context window − completion − guard − overhead).

Include items in order until overflow; the rest → prompt_excluded_ids with reason:"token_budget".

Log math and counts; do not re-order.

10) Templater (deterministic; policy-aware)

Compose strictly from included items.

Never leak raw IDs or hidden fields.

Append the one-liner permissions note iff payload_excluded_ids non-empty.

Respect length/sentence clamps; scrub raw IDs in prose.

11) Target meta (always emitted; fields present even if null)
{
  "request": { "intent": "why_decision|search", "anchor_id": "...", "request_id": "...", "trace_id": "...", "ts_utc": "..." },

  "actor": {
    "user_id": "u-…",
    "role": "staff|manager|director",
    "namespaces": ["public","internal","confidential"],
    "policy_version": "vX",
    "policy_key": "hash-of-role-profile"
  },

  "policy": {
    "policy_id": "why_v1",
    "prompt_id": "why_v1.0",
    "selector_policy_id": "sim_desc__ts_iso_desc__id_asc",
    "allowed_ids_policy": { "mode": "include_all", "cap_k": null, "cap_basis": null, "cap_reason": null },
    "edge_allowlist": ["CAUSAL_PRECEDES","ALIAS_OF"],
    "llm": { "mode": "off|auto|force", "model": "…" },
    "env": { "cite_all_ids": false, "load_shed": false }
  },

  "budgets": { "context_window": 0, "desired_completion_tokens": 0, "guard_tokens": 0, "overhead_tokens": 0 },

  "fingerprints": { "prompt_fp": "sha256:…", "bundle_fp": "sha256:…", "snapshot_etag": "…" },

  "policy_trace": {
    "withheld_ids": ["…"],
    "reasons_by_id": { "id": "acl:role_missing|acl:namespace_mismatch|acl:sensitivity_exceeded" },
    "counts": { "hidden_vertices": 0, "hidden_edges": 0 },
    "edge_types_used": ["CAUSAL_PRECEDES","ALIAS_OF"]
  },

  "evidence_counts": {
    "pool": { "anchor": 1, "events": 0, "transitions": 0, "neighbors": 0, "total": 0 },
    "prompt_included": { "events": 0, "total": 0 },
    "payload_serialized": { "events": 0, "total": 0 }
  },

  "evidence_sets": {
    "pool_ids": ["…"],
    "prompt_included_ids": ["…"],
    "prompt_excluded_ids": [{ "id": "…", "reason": "token_budget" }],
    "payload_included_ids": ["…"],
    "payload_excluded_ids": [{ "id": "…", "reason": "acl:*" }]
  },

  "selection_metrics": {
    "ranking_policy": "…",
    "scores": { "id": { "sim": 0.0, "recency_days": 0, "importance": 0.0 } }
  },

  "truncation_metrics": {
    "passes": [{ "prompt_tokens": 0, "max_prompt_tokens": 0, "action": "rank_and_trim|stop" }],
    "selector_truncation": false,
    "prompt_selector_truncation": false
  },

  "response": {
    "mode": "templater|llm",
    "short_answer": "…",                           // templater path; null on QUERY
    "llm_completion": { "text": "…", "token_usage": { "prompt": 0, "completion": 0 } }, // QUERY path
    "cited_ids": ["…"]
  },

  "runtime": { "latency_ms_total": 0, "stage_latencies_ms": { "preselector": 0, "selector": 0, "gate": 0, "templater": 0 }, "fallback_used": false, "fallback_reason": null, "retries": 0 },

  "validator": { "error_count": 0, "warnings": [] },

  "downloads": {
    "artifacts": [
      { "name": "bundle_view", "allowed": true,  "reason": null, "href": "…" },
      { "name": "bundle_full", "allowed": false, "reason": "acl:sensitivity_exceeded" }
    ]
  }
}


ASK (Staff) → response.mode="templater", short_answer set, llm_completion=null, and typically some payload_excluded_ids.
QUERY (Director) → response.mode="llm", full llm_completion, and usually no payload_excluded_ids.

12) Trace bundle & artifacts (what lives where)

Directory (unchanged names; clarified content):

/trace/
  _meta.json                    ← target meta above (always present; audit source of truth)
  envelope.json                 ← small UI card (templater one-liner, cited_ids, note)
  evidence_pre.json             ← CandidateSet after Pre-Selector (ACL + masking, k=1)
  plan.json                     ← selector policy + per-ID signals (importance, recency, sim, …)
  evidence_post.json            ← post-selector + budget gate (included/excluded with reasons)
  evidence_canonical.json       ← final payload view (what user can download as bundle_view)
  response.json                 ← mode-specific response (short_answer or LLM reference)
  llm_raw.json                  ← raw LLM completion + token usage (QUERY only)
  validator_report.json         ← contract completeness (fields exist even when null)


Downloads

bundle_view → a zip containing: _meta.json, envelope.json, evidence_canonical.json, response.json, plan.json (optional), validator_report.json.

bundle_full → same + pre-policy evidence and hidden items/fields (only if role permits).

13) End-to-end flows
ASK (templater) — Staff

Client → api_edge (auth).

api_edge → Gateway, passes intent + policy headers.

Gateway → Memory API, forwards policy passport + anchor.

Pre-Selector (Memory API): traverse with edge allowlist & domains, ACL filter, field-mask, k=1 → returns evidence_pre + policy_trace.

Gateway: Selector ranks the allowed nodes; Budget Gate clips; Templater composes the envelope and appends permission note if needed.

Gateway writes _meta, evidence_*, envelope, response, etc.

Downloads: bundle_view allowed; bundle_full denied with explicit reason.

QUERY (LLM) — Director

Same headers; Memory API returns richer evidence_pre; Gateway runs Selector → Gate → LLM; llm_raw.json saved; bundle_full allowed.

14) Security, caching & observability

Primary enforcement at source (Memory API) prevents overfetch/confused-deputy.

Defense-in-depth: Gateway re-checks basic ACL on sanitized docs before templating.

Edge leakage: staff can see alias events but not traverse alias edges upward; edge allowlist enforces this.

ID side-channels: keep withheld IDs in _meta.policy_trace (audit only), never in user prose.

Cache partitioning: cache CandidateSets by (anchor_etag, policy_key); never share across roles/namespaces.

k-control: demo uses k=1. If increased later, cap per edge-type budgets to avoid path explosion.

Normalization & injection hygiene: continue normalizing text/HTML; prompt models remain strict (no opportunistic x-extra).

Structured logging (deterministic IDs):

Pre-selector: policy_key, edge types used, neighbors scanned, hidden counts.

Selector: ranking_policy, ordered ID sample, per-ID signals (sampled).

Gate: token math, included/dropped counts, final prompt tokens.

Templater: clamps applied, cited_ids.

15) Short roadmap (minimal, reviewable increments)

M1 — Alias overlay support (edges + ingest)

Add memory/fixtures/edges/aliases/… fixtures.

Ingest CLI: scan, validate RI, upsert edges with type="ALIAS_OF".

Relation catalog: add "ALIAS_OF".

Tests: seed → expand_candidates(anchor) returns alias neighbors with edge.type="ALIAS_OF".

M2 — Pre-Selector in Memory API

Accept policy headers; resolve role profile (domain scopes, sensitivity, field visibility, edge allowlist, k=1).

Traverse with allowlist & scopes; ACL filter; field-mask.

Emit CandidateSet (evidence_pre.json) + PolicyTrace.

Structured logs & contract tests.

M3 — Gateway integration (selector/gate unchanged)

Pass policy headers through; cheap re-checks on sanitized docs.

Persist full trace bundle (files listed above).

Populate _meta including policy_trace, evidence_sets.*, downloads.artifacts.

M4 — Templater permission note & envelope polish

Append note when payload_excluded_ids non-empty.

Verify envelope reflects only cited evidence.

M5 — Downloads & audit drawer

bundle_view and bundle_full gating.

Audit drawer renders: edge types used; withheld counts/reasons; prompt vs payload exclusions.

M6 — Selector confidence & explainability

Emit per-ID scores (importance, recency, sim); show top features for included items (sampled).

M7 — Hardening

Cache partitioning by policy_key.

Replay fingerprints (prompt_fp, bundle_fp).

16) Potential refinements (open to discuss)

Field-masking granularity: keep a whitelist for x-extra subpaths per role (e.g., visibility_note allowed to all; kpis allowed to director).

Edge ACL: tag alias edges with upstream decision's sensitivity so the edge itself can be hidden from staff while the local alias event remains visible.

Gateway models: keep strict by default; if directors need KPIs in prompts, either whitelist a safe subset or surface them in meta only.

Future projection types: this overlay pattern generalizes nicely (ROLLS_UP_TO, ANNOTATES, COUNTERFACTUAL_OF) without touching causal logic.

TL;DR

Add alias overlay edges and ingest them as ALIAS_OF.

Run the Pre-Selector in Memory API (edge allowlist, ACL, masking, k=1).

Gateway remains pure: Selector once → Gate clips in order → Templater composes safely.

Meta is complete & consistent across ASK/QUERY and roles; downloads are permission-gated; audits clearly distinguish policy-withheld vs budget-clipped.



Fixtures Snapshots:

Decision:
{
  "id": "acme-corp-adopt-gpu-platform-2023",
  "option": "Adopt an internal GPU platform and model gateway for AI features",
  "rationale": "Secure GPU capacity, enforce safety/latency controls, and reduce per-inference cost via utilization and caching.",
  "timestamp": "2023-03-30T16:00:00Z",
  "decision_maker": "CTO",
  "tags": ["ai_platform", "latency", "cost_optimization"],
  "supported_by": [
    "acme-e-gpu-shortages-2022-2023",
    "acme-e-latency-slo-misses-2023q1"
  ],
  "based_on": ["acme-corp-unify-cloud-platform-2022"],
  "transitions": [],
  "domain": "acme/corporate",
  "importance": 0.87,
  "sensitivity": "high",
  "namespaces": ["internal", "confidential"],
  "roles_allowed": ["director", "exec"],
  "x-extra": {
    "kpis": [
      {
        "name": "Inference cost",
        "unit": "USD/1k tokens",
        "baseline": 0.90,
        "target": 0.35
      },
      {
        "name": "p95 latency",
        "unit": "ms",
        "baseline": 850,
        "target": 300
      },
      {
        "name": "GPU utilization",
        "unit": "%",
        "baseline": 28,
        "target": 65
      }
    ],
    "kpi_tracking_id": "KPI-AI-2023-03"
  }
}


Event:

{
  "id": "acme-e-alias-descope-onprem-2024",
  "summary": "Corporate announces de-scoping of on-prem SKU by 2025",
  "description": "Executive decision impacts product roadmap; details limited to scope and timeline.",
  "timestamp": "2024-02-12T14:00:00Z",
  "tags": ["alias_event", "corporate_directive", "cloud_only"],
  "led_to": ["acme-prod-pivot-cloud-only-tiers-2024", "acme-prod-sunset-onprem-connectors-2024"],
  "snippet": "Corporate directive: de-scope on-prem by 2025.",
  "domain": "acme/product",
  "importance": 0.9,
  "sensitivity": "medium",
  "namespaces": ["internal"],
  "roles_allowed": ["manager", "director", "exec"],
  "x-extra": {
    "alias_of_decision": "acme-corp-descope-onprem-2024",
    "visibility_note": "Projection of executive decision; rationale withheld at this level."
  }
}

Transition: 

{
  "id": "trans-acme-a1-to-a2",
  "from": "acme-corp-unify-cloud-platform-2022",
  "to": "acme-corp-adopt-gpu-platform-2023",
  "relation": "causal",
  "reason": "Unified platform enabled centralized inference and capacity planning.",
  "timestamp": "2023-03-30T16:00:00Z",
  "domain": "acme/corporate",
  "x-extra": {}
}

Edge-Alias

{
  "id": "alias-acme-a3-to-product",
  "type": "alias_event",
  "decision_id": "acme-corp-descope-onprem-2024",
  "event_id": "acme-e-alias-descope-onprem-2024",
  "scope": "projection",
  "domain_from": "acme/corporate",
  "domain_to": "acme/product",
  "x-extra": {
    "note": "Graph overlay: shows executive decision projected as product-level event"
  }
}