PATCH 1

We replaced the ambiguous, flat meta with a JSON-first, nested schema that cleanly separates Pool vs Prompt vs Payload, records policy/budgets/fingerprints, and adds deterministic audit logs—so inconsistencies like the pan-e11/e3 case can’t happen and runs are reproducible.
Backend-only: we updated Pydantic models and the builder/gate to emit the new shape (and fixed gate control flow), removed legacy fields/back-compat, and made ask runs explicit (llm.mode=off, payload_source=pool).

What we fixed

Root problem: the trace said prompt_dropped_event_ids=["pan-e11","pan-e3"] yet those IDs still appeared in the final evidence. Cause: a prompt-time vs payload-time mismatch (and LLM off on ask runs), plus flat/ambiguous meta fields that conflated stages.

What we changed (backend-only)

New JSON-first meta schema (no back-compat):
request, policy, budgets, fingerprints, evidence_counts, evidence_sets, selection_metrics, truncation_metrics, runtime, validator.

Pool / Prompt / Payload separation:

evidence_sets.pool_ids = everything we can find (allowed).

evidence_sets.prompt_included_ids & prompt_excluded_ids[{id, reason}] = what actually went into the prompt and why anything was trimmed.

evidence_sets.payload_included_ids + payload_source = what we returned and where it came from.

Policy snapshot: env-driven knobs captured in policy (incl. allowed_ids_policy, llm.mode, selector_policy_id, gateway_version).

Budgets & truncation: token windows + per-pass truncation metrics (passes with tokens/limits/actions; selector_truncation flags).

Fingerprints for replay: prompt_fp, bundle_fp, snapshot_etag.

Strategic structured logging: concise, deterministic audit logs at gate/builder (selector_complete, prune_exit, pool_prompt_payload, fingerprints).

Strict schema enforcement: removed legacy/flat fields (prompt_evidence_metrics, evidence_metrics, dropped_evidence_ids, events_truncated, etc.). Pydantic extra="forbid" now passes because builders emit only the new shape.

Files touched (high level)

budget_gate: corrected indentation & control-flow; authoritative early returns; emits prompt_included_ids / prompt_excluded_ids[{id,reason}]; sampled ID logging.

builder: removed legacy MetaInfo(...) path; builds nested MetaInputs; calls build_meta(...) once; ensures artefacts are bytes; guarantees settings availability.

meta_inputs / meta_builder / models: added the new nested models; canonical assembly to MetaInfo; one concise audit log.

Ask-specific behavior

Same meta shape.

policy.llm.mode="off"; runtime.fallback_used=true with fallback_reason="llm_off"; evidence_sets.payload_source="pool".

This makes the earlier pan-e11/pan-e3 confusion impossible: prompt-time drops are clearly scoped, while the payload’s source is explicit.

Ultimate goal

Audit-grade transparency & reproducibility: anyone can answer “what was considered vs prompted vs returned, and why” at a glance; fingerprinted envelopes enable replay.

Policy-driven, env-driven operation: default include-all allowed IDs; if latency rises later, switch to deterministic top-K caps (with reasons recorded) without losing auditability.

Cleaner frontend audit drawer: SSE-streamable counts, passes, and reasons; deterministic logs keyed by request/trace IDs.


PATCH 1




