# Observability & Metrics Implementation Plan

## What still needs to happen (observability-wise) before we can close out Milestone 3
Below is a gap checklist organised by signal type (metrics ▹ traces ▹ logs ▹ artifacts ▹ dashboards/alerts). Items already satisfied by the new core_metrics package are crossed out. Everything else must be implemented or verified.

## Overview
This document outlines the concrete work remaining to implement comprehensive observability across all services, including metrics, traces, structured logs, artifact trails, and monitoring infrastructure.

## Implementation Areas

### 1. Metrics Implementation

#### 1.1 Instrument Service Calls
**Status**: Runtime helper exists (`core_metrics`)

**Required Instrumentation by Service**:

- **Gateway Selector**:
  - `total_neighbors_found`
  - `selector_truncation`
  - `final_evidence_count`
  - `bundle_size_bytes`
  - `dropped_evidence_ids[]`

- **Resolver**:
  - `resolver_confidence` (histogram)
  - `cache_hit_total`

- **Memory-API & Ingest Caches**:
  - Hit/miss counters

- **API-Edge**:
  - TTFB histogram
  - Fallback counter

**Reference**: "Evidence, model & bundle metrics surfaced" milestone requirements + metric list in B5 tech-spec

#### 1.2 Metrics Exposure
**Requirement**: Expose `/metrics` endpoint (Prometheus) or OTLP exporter in every container

**Current State**: OTEL Collector is already in compose but endpoints/env-vars must be added to each Dockerfile

**Testing**: Health-check tests expect it (`test_gateway_health.py`)

#### 1.3 CI Metrics Testing
**Requirement**: Add smoke test that scrapes each `/metrics` at startup and asserts presence of required metric names

**Status**: Not yet covered by tests (good safeguard needed)

---

### 2. Distributed Tracing (OTEL Spans)

#### 2.1 Stage Coverage Verification
**Requirement**: Confirm all nine stages are wrapped in spans for every request path (slug & search)

**Nine Required Stages**:
1. resolve
2. plan
3. exec
4. enrich
5. bundle
6. prompt
7. llm
8. validate
9. render
10. stream

**Reference**: Structured-span requirement in B5 tech-spec; Milestone-2 "OTEL spans for all stages"

#### 2.2 Span Attributes
**Requirement**: Add span attributes listed in spec (IDs, model IDs, etc.)

**Reference**: Deterministic-ID list in B5 tech-spec

---

### 3. Structured Logging

#### 3.1 Log Format Compliance
**Requirement**: Verify log envelope matches JSON format shown in B5, including new evidence fields

**New Evidence Fields**:
- `selector_truncation`
- Additional fields as specified in B5 tech-spec

**Reference**: Generic log envelope in B5 tech-spec

#### 3.2 Log Storage & Testing
**Requirement**: Ensure logs are shipped/stored and appear in unit tests that parse logs

**Status**: No Milestone-3 gate, but must appear in unit tests

**Reference**: Tests parse logs for audit metadata (`test_gateway_audit_metadata.py`)

---

### 4. Artifact Trail Management

#### 4.1 Artifact Storage Verification
**Requirement**: Double-check artifact-sink code is wired for every stage and stores to MinIO under `/{request_id}/` with correct filenames

**Reference**: "Complete artifact trail per request" requirement

#### 4.2 Artifact Metrics
**Requirement**: Add `artifact_bytes_total` gauge or counter to track size growth over time

**Reference**: Recommended in B5 Metrics list tech-spec

---

### 5. Monitoring & Alerting

#### 5.1 Grafana Dashboards
**Required Dashboards**:
- P95 latency
- Cache hit-rate
- Selector truncation frequency

#### 5.2 Alert Rules
**Required Alerts**:
- SLO breaches for P95 > 3s
- Error rate > 0.1%

**Reference**: Monitoring targets in milestones doc

---

### 6. Testing & Continuous Integration

#### 6.1 Unit Test Coverage
**Requirement**: Add unit test stubs for new metrics (names present, labels correct) to keep mapping file green

**Status**: Test-mapping file shows metric tests already exist but may expect new names soon

**Reference**: Milestone requirements testing documentation

---

## Implementation Priority

1. **High Priority**: Metrics instrumentation and exposure (items 1.1-1.3)
2. **High Priority**: Span coverage verification (item 2.1)
3. **Medium Priority**: Structured logging compliance (items 3.1-3.2)
4. **Medium Priority**: Artifact trail verification (items 4.1-4.2)
5. **Low Priority**: Dashboards and alerting (items 5.1-5.2)
6. **Ongoing**: Test coverage maintenance (item 6.1)

## References

- B5 tech-spec: Technical specifications document
- Milestone requirements documentation
- Test mapping files
- Project development milestones documentation