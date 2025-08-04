1 · Accurate / Fast Retrieval
Iteration 2 (Neo4j + Chroma)	V2 (Arango Graph + Vector)
Stores	• Neo4j Bolt cluster for the graph
• ChromaDB collection for text embeddings (768-d SBERT)	• Single ArangoDB instance – both graph & HNSW vector index live side-by-side (no cross-service hop) tech-spec
Resolver logic	1. If the decision_ref looks like a slug, short-circuit to Neo4j
2. Else embed query → dense vector search in Chroma (top k=5)
3. BM25 fallback inside Neo4j’s full-text index	Same short-circuit + search flow but consolidated behind /api/resolve/text, so the Gateway never knows which index answered; it simply gets a confidence score and an anchor.id tech-spec
Ranking / tie-breaks	Hard-coded recency factor + cosine similarity weighting	Resolver is pluggable (“weak-AI” bi-encoder today, cross-encoder later); ranking is hidden behind the endpoint and is metrics-tracked (resolver_confidence) for drift alerts tech-spec
Caching	In-process OrderedDict LRU (~128 entries) per service instance	Distributed Redis caches (5 min resolver, 1 min expand) with ETag invalidation and OTEL hit/miss metrics milestone_reqs_to_test_…

Take-away
Iteration 2 got you decent precision, but the dual-store hop and per-instance cache made tail latency unpredictable. V2 collapses everything into one physical DB and surfaces confidence + cache metrics to the Gateway so selection can be audited.

2 · Graph Hops (Neighbour Expansion)
Iteration 2	V2
Implementation	Hand-written Cypher for each intent (e.g. WHY: `MATCH (d:Decision {id:$id})-[:PRECEDES	:CAUSAL]->(n)…`)
Edge types	Fixed set baked into the Cypher (PRECEDES, CAUSAL, SUPPORTED_BY)	Edge catalogue served live by /api/schema/rels; you can add CHAIN_NEXT later with zero Gateway change tech-spec
Hop limit	k was hard-coded per query (mostly 1)	Still k = 1 for “why/who/when” today, but it is a parameter in the plan so future intents can walk deeper without new code
Completeness vs. prompt size	Neighbours were already truncated in Neo4j query (LIMIT 20)	Gateway always collects all k=1 neighbours; size checks happen later with the selector model (see §3) tech-spec

3 · Enrichment & Evidence-Bundle Building
Iteration 2	V2
Where it happens	Same Memory-API service built an ad-hoc “enriched decision” JSON (events, transitions, metadata)	Two-phase: Memory-API only returns normalized envelopes; Gateway assembles the Evidence Bundle and logs its fingerprint for audit tech-spec
Schema control	Implicit Pydantic model; adding a new field (e.g. tags) required service redeploy	Field & Relation catalog endpoints publish aliases; Gateway accesses everything via get_field(node,"…") so adding phase_label is zero-code tech-spec
Size management	None – whatever Neo4j returned was sent straight to the LLM, often blowing past 8 k tokens	Hard limit MAX_PROMPT_BYTES = 8192; when hit, a tiny GBDT selector scores items (similarity, recency, degree) and logs selector_truncation=true tech-spec
Audit artefacts	Basic Prometheus timings	Full artefact chain (Bundle → Prompt Envelope → raw LLM JSON → Validator report) stored in MinIO and addressable by request_id for replay tech-spec

4 · Performance Envelope
Iteration 2	V2 Targets
p95 latency	Ask (slug) ≈ 4.5 s; heavy variance due to cold-start Chroma calls	Hard budgets: Search ≤ 0.8 s, Expand ≤ 0.25 s, Enrich ≤ 0.6 s, total /v2/ask p95 ≤ 3 s tech-spec
Load shedding / fallbacks	None; timeout bubbled 5xx to FE	Auto-switch to llm_mode=off (templater) and set meta.load_shed=true when budgets breached tech-spec
Validation	LLM JSON occasionally leaked bad IDs; front-end crashed	Blocking validator enforces schema and ID-scope; after 2 retries it falls back to templater with fallback_used=true so users never see junk tech-spec
Observability	Basic counters	OTEL spans for every stage plus deterministic prompt_fingerprint, bundle_fingerprint, etc., in every log line tech-spec

End-to-End Flow Comparison
Iteration 2:

nginx
Copy
Edit
FE → /search       → (Chroma or Neo4j) → list of Decision IDs
   → /decision/:id → Neo4j Enrichment   → FE renders
(No central Gateway, no bundle cache, no selector, no audit trail)
V2:

bash
Copy
Edit
FE → /v2/ask or /v2/query       (single endpoint)
API-Edge → Gateway
   1. Resolver (/api/resolve/text)      → anchor.id, confidence
   2. Planner builds SA-GQL plan
   3. Memory-API /graph/expand_candidates → all k=1 neighbours
   4. Gateway bundles evidence, caches, trunks if >8192 B
   5. Prompt Envelope → LLM (JSON-mode, ≤2 retries)
   6. Validator → stream short_answer (or templater fallback)
   7. Artefacts persisted, metrics emitted
Key Lessons & Design Rationale
Unify the data plane. Neo4j + Chroma gave good flexibility but doubled cold-start and consistency complexity. A single Arango instance with built-in vector search removes an entire network hop and lets AQL traverse & vector-filter in one shot.

Move “smart” logic to the Gateway. Iteration 2’s Memory-API mixed lookup, enrichment and evidence trimming so the bundle was opaque. In V2, Memory-API is a deterministic enrichment service; only the Gateway may drop evidence, and every drop is logged.

Make every decision auditable. Fingerprinting the canonical Prompt Envelope plus artefact retention means you can replay and diff two answers token-for-token – a must for compliance and model-drift debugging.

Budget everything. Performance ceilings (per-stage timeouts, prompt byte caps) and automatic load-shedding avoid the tail-latency spikes we saw when Chroma warmed up.

Bottom Line
Iteration 2 proved the concept, but relied on service-local caches, bespoke Cypher, and implicit schemas. V2 generalises the same core steps into schema-agnostic contracts, a unified graph / vector store, and a Gateway that enforces budgets, validation and detailed audit-trails. Those changes squarely target the pain-points you listed: retrieval accuracy, hop management, enrichment transparency and predictable performance.



1. Iteration 2: The TraceBuilder in /src/batvault_core
What it was

A per-request, in-process collector (class TraceBuilder in /src/batvault_core/trace_builder.py) that:

Generated a trace_id (UUID) and wrote a JSON file under llm_cache/traces/ with a timestamped filename.

Captured stages, timings, events, logs, token‐stream chunks, and answer metadata into a single nested dict.

Hooked into Python’s root logger via a TraceCaptureHandler to capture log records with extra fields.

Exposed a minimal FastAPI TraceService in the gateway that listed (GET /traces) and retrieved (GET /traces/{trace_id}) these JSON blobs.

Key methods

add_stage(name, duration_ms, error) → appends to trace["stages"]

add_timing(name, ms) → records in trace["timings"]

add_event(name, payload) → logs pipeline events (e.g. “bundle_ready”)

add_token(token, cost, source) → buffers per-token info (for audits/LTR)

Imperfections

Local only: Traces lived on each service’s filesystem, making it hard to correlate across multiple instances or microservices.

No global correlation: Each service wrote its own trace JSON; there was no single request_id propagated end-to-end.

Loose schema: The JSON files weren’t versioned or schema-validated, leading to drift as stages evolved.

No artifact linkage: Traces didn’t tie into the prompt envelope, raw LLM JSON, or validator reports stored elsewhere.

Operational overhead: Writing files on disk for every request added I/O latency and complexity around cleanup/retention.

2. V2: Distributed, OTEL-Centric Tracing & Durable Artifacts
a) OpenTelemetry Spans & Structured Logs
Instrumented stages end-to-end as named OTEL spans:
resolve → plan → exec → enrich → bundle → prompt → llm → validate → render → stream tech-spec

Generic log envelope enriched with:

request_id (the single correlation key)

snapshot_etag, prompt_fingerprint, bundle_fingerprint

Stage-specific meta (evidence counts, selector truncation flags, latencies) project_development_mil…

Automatic context propagation across FastAPI/Uvicorn, Redis, ArangoDB, and MinIO calls, so every span/log carries the same request_id.

b) Durable, S3-Style Artifact Retention
All intermediate artifacts (resolver results, graph plan, evidence bundle pre/post truncation, prompt envelope, rendered prompt, raw LLM request & response, validator report, final response) are written to MinIO under /{bucket}/{request_id}/… tech-spec.

Each object is content-addressable (filename = SHA256 fingerprint), so it’s immutably versioned and easy to fetch.

c) Unified Trace Service
Our enhanced /api/traces/{request_id} now:

Aggregates OTEL spans (from the collector) and all S3 artifacts.

Streams back a single “evidence atlas” JSON that ties every span, log entry, and artifact file together in chronological order.

Supports both snake_case and lowerCamelCase formats, plus raw streaming for large JSON blobs.

3. Why It’s Fundamentally Different
Aspect	Iteration 2 (TraceBuilder)	V2 (OTEL + Durable Artifacts)
Storage	Local JSON files per instance	Centralized S3/MinIO bucket per request_id
Correlation	One trace per service, no global request_id	One request_id flows through all services
Schema & Validation	Ad-hoc Python dict, no contract	OTEL span schemas + JSON artefact schemas (versioned)
Cross-Service	Manual retrieval via service-specific endpoints	Single TraceService merges spans + artefacts
Retention & Cleanup	Manual or TTL-based file cleanup	Configurable MinIO lifecycle policies per tenant
Overhead	File I/O on every request, no batching	Highly optimized OTEL exporter + async S3 writes
Audit UX	Basic JSON download	Rich “Audit Drawer” UX in frontend, showing envelope → spans → artifacts

In short, we’ve moved from a lightweight, stand-alone TraceBuilder to a production-grade, distributed tracing and artifact management framework. This not only solves the cross-service correlation and durability gaps but also aligns us with best practices (OpenTelemetry, immutable artifacts, content addressing) for robust observability and compliance.






1. Global Versioning with snapshot_etag
Iteration 2:

Neo4j and Chroma refreshed independently.

Caches were per-service LRU maps with no unified invalidation.

V2:

Batch snapshot: As soon as new JSON is ingested, the Ingest V2 pipeline computes a content hash over the entire batch and emits a snapshot_etag (J1.1) tech-spec.

Propagated everywhere:

Written into every ArangoDB upsert (graph + vector)

Tagged on all Memory-API responses (via an HTTP ETag header)

Included in every log span from resolve → bundle → prompt → llm → validate tech-spec

Cache invalidation: Any change to the corpus changes the snapshot_etag, which triggers downstream caches (resolver, bundle, LLM) to invalidate atomically.

Why it matters: There is now a single, authoritative version stamp for your entire graph+vector store, so you never accidentally serve mixed-version data.

2. Declarative, Versioned Envelopes
Iteration 2:

Memory-API enriched Neo4j rows directly; adding new fields (e.g. tags) required code changes and a redeploy.

V2:

Normalized JSON envelopes: Every /api/enrich/{type}/{id} call returns a canonical envelope that strictly follows the V2 authoring schemas (Decision, Event, Transition) tech-spec.

Enforces referential integrity only when non-empty arrays are present

Normalizes text (NFKC → trimmed) and timestamps (ISO-8601 UTC)

Aliases user synonyms into core fields, preserving originals in x-extra

Schema-agnostic catalogs: The Field & Relation Catalog endpoints drive enrichment logic, so adding a new field on the JSON side automatically appears in evidence bundles with zero service changes.

Why it matters: You get guaranteed, consistent data shapes that evolve under schema governance, not hidden code paths.

3. Auditable Prompt Envelopes & Fingerprints
Iteration 2:

Prompts were ad-hoc strings assembled from Neo4j responses. No record of exactly what went into the LLM.

V2:

Prompt Envelope construction (C.1) tech-spec

json5
Copy
Edit
{
  "prompt_version":"why_v1",
  "intent":"why_decision",
  "policy":{"json_mode":true,"retries":2,"temperature":0.0},
  "question":"…",
  "evidence":{/* minified bundle */},
  "allowed_ids":[/* IDs anchor ∪ events ∪ transitions */],
  "constraints":{"output_schema":"WhyDecisionAnswer@1","max_tokens":256},
  "explanations":{/* why these fields/IDs */}
}
Canonicalization & Fingerprinting (C.2)

Sort keys & normalize whitespace → produce a canonical JSON

SHA-256 over that canonical form → prompt_fingerprint tech-spec

Artifact retention: The Gateway writes both the raw envelope and its fingerprint to object storage under s3://…/{request_id}/ alongside every other artifact.

Why it matters: Every LLM request is now a pure function of (intent, evidence, policy), with a unique, reproducible fingerprint. You can diff two envelopes token-for-token or replay them exactly.

4. Blocking Validation & Immutable Traces
Iteration 2:

Occasional front-end crashes when LLM returned malformed JSON or invalid IDs.

Only Prometheus metrics; no stored LLM outputs to debug against.

V2:

Full artefact chain (resolver results, graph plan, pre/post truncation bundles, envelope, rendered prompt, raw LLM JSON, validator report, final response) is persisted per request_id tech-spec.

Blocking validator:

Parses LLM output against the declared schemas (WhyDecisionAnswer@1, WhyDecisionResponse@1)

Verifies supporting_ids ⊆ allowed_ids, mandatory anchor/transition citations, field‐level constraints

On failure → retry up to 2× → fallback to templater (fallback_used=true) tech-spec

Why it matters: Users never see invalid or stale data. Every inconsistency is caught, recorded, and can be debugged via the replay endpoint.

Fundamental Contrast with Iteration 2
One global version stamp (snapshot_etag) vs. scattered per-instance caches.

Explicit, versioned JSON contracts vs. implicit Pydantic models that hid schema changes.

Pure functional envelopes + fingerprints vs. ad-hoc prompt stitching.

Full, persistent audit trail + blocking schema checks vs. best-effort timing metrics.

Iteration 2 proved the concept, but V2 elevates data consistency into a first‐class, deterministic contract: every bit of evidence, every prompt token, every model output is versioned, fingerprinted, schema-enforced and auditable. The result is rock-solid reproducibility, compliance-grade tracing, and zero surprises in production.



1. Data Plane Unification
Iteration 2:

Dual stores—Neo4j for graph, ChromaDB for vector search—accessed by separate services in batvault_memory_api/services/graph_expander.py and batvault_memory_api/services/search.py.

Per-instance OrderedDict LRU caches (size 128) for graph expansion and a simple in-process cache for vector results.

V2:

Single ArangoDB instance holding both graph collections and a built-in HNSW vector index, eliminating cross-service hops; all upserts and queries go through one storage adapter tech-spec.

Distributed Redis caches (5 min resolver, 1 min expand) invalidated atomically on every new snapshot_etag project_development_mil….

2. Schema-First Enrichment vs. Ad-Hoc Models
Iteration 2:

Bulk enrichment via hard-coded Cypher in /services/enrichment.py, Pydantic models in batvault_memory_api/models/public.py drove JSON shape; adding a field meant touching code & redeploying.

V2:

Versioned JSON Schemas (e.g. WhyDecisionEvidence@1, WhyDecisionAnswer@1) are the source of truth; the Memory API normalizes everything per schema and publishes Field & Relation Catalog endpoints so new fields surface with zero code changes tech-spec.

3. Tracing & Auditability
Iteration 2:

A custom TraceBuilder in batvault_core/trace_builder.py spun up a UUID, captured stages/timings/events, and wrote a local JSON file per request—isolated to each service’s filesystem, with no global correlation or schema validation.

V2:

End-to-end OpenTelemetry spans named resolve→plan→exec→enrich→bundle→prompt→llm→validate→render→stream, all tagged with one request_id, snapshot_etag and deterministic fingerprints tech-spec.

Immutable, content-addressed artifacts (resolver results, graph plan, evidence bundle pre/post-truncation, prompt envelope, raw LLM I/O, validator report, final JSON) are persisted in MinIO/S3 under {request_id} tech-spec, and surfaced in a single /api/traces/{request_id} audit view.

4. Evidence Bundling & Size Management
Iteration 2:

Neighbours truncated server-side in Neo4j query (LIMIT 20), then everything was naively serialized into the prompt—often overshooting token budgets.

V2:

Unbounded k=1 collection of all first-degree neighbours; then a selector model (GBDT/log-reg today) steps in only if json.dumps(bundle).length > MAX_PROMPT_BYTES = 8192 to drop lowest-scoring items, logging selector_truncation=true tech-spec.

Guarantees at least MIN_EVIDENCE_ITEMS = 1 alongside the anchor, and records full feature snapshots for every dropped node.

5. Resolver & Ranking
Iteration 2:

A two-stage resolver: check slug short-circuit → SBERT-based Chroma query → BM25 fallback inside Neo4j’s full-text index; scoring was hard-coded (1 – distance + recency bonus).

V2:

A pluggable resolver interface behind /api/resolve/text: slug short-circuit, BM25 lexical, bi-encoder vector (feature-flagged), with each method returning a unified confidence score. All resolution logic is metrics-tracked (resolver_confidence) to detect drift requirements_checklist.

6. Error Handling & Fallback Semantics
Iteration 2:

If the LLM spit back malformed JSON or invalid IDs, users would see a 5xx or a client-side crash—no graceful degradation.

V2:

A blocking validator enforces schema & ID-scope (supporting_ids ⊆ allowed_ids), retries up to 2×, then fails over to a deterministic templater with fallback_used=true—guaranteeing users never see broken output tech-spec.

7. Performance Budgets & Load Shedding
Iteration 2:

No stage-level budgets; tail latency spiked whenever Chroma cold-started.

V2:

Strict p95 targets: Search ≤ 800 ms, Graph ≤ 250 ms, Enrich ≤ 600 ms, total /v2/ask ≤ 3 s; each stage is wrapped in asyncio.wait_for.

Auto load-shedding: if budgets breach, the Gateway switches llm_mode=off, sets meta.load_shed=true, and returns templated answers to preserve SLOs tech-spec.

8. Testing & Quality Gates
Iteration 2:

Limited unit tests around individual services and ad-hoc performance checks.

V2:

Golden tests for every intent (coverage = 1.0, completeness_debt = 0).

Contract tests for /v2/ask, /v2/query, Memory-API enrichment & resolve/expand endpoints.

Integration, performance, fallback and resilience tests in CI, all wired into Docker Compose/K8s pipelines project_development_mil….

Bottom Line
Iteration 2 nailed the core idea—surface related decisions & events via a graph+vector hop and feed them to an LLM—but relied on dual stores, ad-hoc schemas, local-only tracing, and no formal budgets or fallbacks. V2 re-architects every piece around single-store consistency, schema-first envelopes, distributed OTEL tracing & artifact retention, learned & pluggable weak-AI components, and test-driven performance & reliability guarantees—making the system predictable, auditable and ready to scale.