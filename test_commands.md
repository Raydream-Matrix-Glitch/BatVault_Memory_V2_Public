# API Testing Guide

## 1) Health / Ready / Metrics

### API Edge

```bash
curl -s -i http://localhost:8080/healthz
```
Expect: 200 OK, Content-Type: application/json. Body: {"status":"ok","service":"api_edge"} (shape isn't normative, but must be JSON).

```bash
curl -s -i http://localhost:8080/readyz
```
Expect: 200 OK when dependencies (ArangoDB/Redis/MinIO) are reachable; 503 otherwise. JSON body should mention dependency readiness.

```bash
curl -s http://localhost:8080/metrics | head -n 20
```
Expect: Prometheus text exposition (starts with # HELP / # TYPE). You should see process/runtime counters plus service metrics (latency, fallback rate, cache hits) as defined in the spec's Metrics section.

### Gateway

```bash
curl -s http://localhost:8081/healthz || echo "Gateway exposes readiness via API Edge proxy only"
```
Expect: Either 200 with JSON (if exposed) or your echo fallback. (Spec requires health/ready endpoints on every service; whether 8081 is directly exposed depends on your compose.)

```bash
curl -s http://localhost:8081/metrics | head -n 20
```
Expect: Same Prometheus format; gateway-specific metrics like selector_truncation counts appear here.

### Memory API

```bash
curl -s -i http://localhost:8082/healthz
```
Expect: 200 OK with JSON.

```bash
curl -s http://localhost:8082/metrics | head -n 20
```
Expect: Prometheus metrics; watch for snapshot_etag labels surfacing in request metrics.

### Ingest

```bash
curl -s -i http://localhost:8083/healthz
```
Expect: 200 OK with JSON. In logs you should see structured batch logs including snapshot_etag, nodes/edges loaded.

```bash
curl -s http://localhost:8083/metrics | head -n 20
```
Expect: Prometheus metrics (batches processed, validation errors).

## 2) API contracts — /v2 via API Edge

### Structured /v2/ask (JSON)

```bash
curl -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | jq .
```

Expect (200 + JSON): Body conforms to WhyDecisionResponse@1:

- intent: "why_decision"
- evidence: {anchor, events[], transitions{preceding?,succeeding?}, allowed_ids[]}
  (k=1 neighbors; for this anchor you should see events pan-e2, pan-e3, pan-e11 and transitions trans-pan-2010-2012, trans-pan-2012-2014)
- answer: {short_answer, supporting_ids[] ⊆ allowed_ids} with anchor and present transition IDs cited
- completeness_flags: {"has_preceding":true,"has_succeeding":true,"event_count":3}
- meta: includes {policy_id, prompt_id, retries, latency_ms, prompt_fingerprint, snapshot_etag, fallback_used:false}.

Good example (trimmed):

```json
{
  "intent": "why_decision",
  "evidence": {
    "anchor": {"id":"panasonic-exit-plasma-2012","rationale":"Declining demand...EV batteries","timestamp":"2012-04-30T09:00:00Z"},
    "events": [
      {"id":"pan-e2","summary":"¥913 m operating loss in plasma division","timestamp":"2011-10-09T05:30:00Z"},
      {"id":"pan-e3","summary":"TV business posts $913 m operating loss","timestamp":"2011-10-09T00:00:00Z"},
      {"id":"pan-e11","summary":"Samsung posts 20% YoY LCD TV growth","timestamp":"2011-11-15T08:00:00Z"}
    ],
    "transitions": {
      "preceding":[{"id":"trans-pan-2010-2012","from":"panasonic-tesla-battery-partnership-2010","to":"panasonic-exit-plasma-2012"}],
      "succeeding":[{"id":"trans-pan-2012-2014","from":"panasonic-exit-plasma-2012","to":"panasonic-automotive-infotainment-acquisition-2014"}]
    },
    "allowed_ids": ["panasonic-exit-plasma-2012","pan-e2","pan-e3","pan-e11","trans-pan-2010-2012","trans-pan-2012-2014"]
  },
  "answer": {
    "short_answer": "Panasonic exited plasma after sustained losses and a market shift to LCDs, reallocating focus to EV batteries and automotive electronics.",
    "supporting_ids": ["panasonic-exit-plasma-2012","pan-e2","pan-e11","trans-pan-2010-2012","trans-pan-2012-2014"]
  },
  "completeness_flags":{"has_preceding":true,"has_succeeding":true,"event_count":3},
  "meta":{"policy_id":"why_v1","prompt_id":"summ_v1","retries":0,"latency_ms":1200,"prompt_fingerprint":"sha256:...","snapshot_etag":"sha256:...","fallback_used":false}
}
```

Contract notes: validator enforces schema + ID scope; if LLM JSON fails, you still get a valid response with fallback_used=true.

### Structured /v2/ask (SSE streaming)

```bash
curl -sN -X POST 'http://localhost:8080/v2/ask?stream=true' \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}'
```

Expect (200 + SSE):
Headers: Content-Type: text/event-stream, Cache-Control: no-cache, Connection: keep-alive.
Behavior: service buffers & validates then streams tokenized answer.short_answer. Typical lines:

```yaml
id: req_...
data: Panasonic exited plasma after sustained losses...
data:  and a market shift to LCDs...
data:  reallocating focus to EV batteries...
event: done
data: {"fallback_used": false, "latency_ms": 1200}
```

If validation fails after ≤2 retries, it streams the templated fallback and sets fallback_used:true.

### Structured /v2/ask (SSE + event framing)

```bash
curl -sN -X POST 'http://localhost:8080/v2/ask?stream=true&include_event=true' \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}'
```

Expect: Same as above, but each token line may include an event: (e.g., event: chunk) plus a terminal event: done. Event names are not normative in the spec; the key guarantee is that only the validated short answer is streamed.

### Natural-language /v2/query (JSON)

```bash
curl -s -X POST http://localhost:8080/v2/query \
  -H 'Content-Type: application/json' \
  -d '{"text":"Why did Panasonic exit plasma TV production?"}' | jq .
```

Expect (200 + JSON): Same WhyDecisionResponse@1 shape, plus meta details about routing:

```json
"meta": {
  "function_calls": ["search_similar","get_graph_neighbors"],
  "routing_confidence": 0.80,
  "policy_id": "query_v1",
  "prompt_id": "router_v1",
  "retries": 0,
  "latency_ms": 1400,
  "prompt_fingerprint":"sha256:...",
  "snapshot_etag":"sha256:...",
  "fallback_used": false
}
```

Router must call Memory API functions in this order: search_similar(text,k) → get_graph_neighbors(node_id,k).

### Natural-language /v2/query (SSE)

Same as /v2/ask?stream=true: tokenized short_answer only, post-validation.

### Schema (mirrored via API Edge)

```bash
curl -s http://localhost:8080/v2/schema/fields | jq .
curl -s http://localhost:8080/v2/schema/rels | jq .
```

Expect (200 + JSON):

- fields maps semantic names to aliases, e.g. {"rationale":["rationale","why","reasoning"], "snippet":["snippet"], "based_on":["based_on"] ...}
- rels lists edge types: ["LED_TO","CAUSAL_PRECEDES","CHAIN_NEXT"].

These must reflect whatever ingest derived from the current snapshot (schema-agnostic proof).

### Error probes

#### Missing anchor_id

```bash
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' -d '{"intent":"why_decision"}'
```

Expect: 400 Bad Request with Error Envelope:

```json
{"error":{"code":"VALIDATION_FAILED","message":"anchor_id required","details":{"reasons":["missing: anchor_id"]},"request_id":"req_..."}}
```

#### Unknown slug

```bash
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"non-existent-decision-xyz"}'
```

Expect: 404 and

```json
{"error":{"code":"ANCHOR_NOT_FOUND","message":"anchor not found: non-existent-decision-xyz","details":{},"request_id":"req_..."}}
```

Error envelope format is normative.

## 3) Gateway internal helpers

```bash
curl -s -X POST http://localhost:8081/ops/minio/ensure-bucket | jq .
```
Expect: {"ok":true,"bucket":"batvault-artifacts"} (name may vary via env). Purpose: ensure S3/MinIO bucket for artifacts (prompt envelopes, raw LLM JSON, validator reports, final responses).

```bash
curl -s http://localhost:8081/evidence/panasonic-exit-plasma-2012 | jq .
```
Expect: The Evidence Bundle the gateway would feed to the LLM: {anchor, events[], transitions{}, allowed_ids[]} as described above; if over budget, selector_truncation:true appears in logs (not the body).

✅ Your two "inside container" equivalents via docker compose exec api_edge are correct.

Minor ambiguity: you also have POST http://localhost:8080/ops/minio/bucket. If API Edge proxies gateway ops, both can work; otherwise prefer the 8081 helper you already use. (No normative path for this in the spec.)

## 4) Memory API (authoritative normalization layer)

### Schema

```bash
curl -s http://localhost:8082/api/schema/fields | jq .
curl -s http://localhost:8082/api/schema/rels   | jq .
curl -s http://localhost:8082/api/schema/relations | jq .
```

Expect: Same as /v2/schema/*, but this is the source of truth; gateway mirrors it. Must include newly supported fields: tags, based_on, snippet, x-extra.

### Enrichment

```bash
curl -s http://localhost:8082/api/enrich/decision/panasonic-exit-plasma-2012 | jq .
```

Expect: Normalized envelope:

```json
{"id":"panasonic-exit-plasma-2012","option":"Exit plasma TV production",
 "rationale":"Declining demand ... EV batteries.","timestamp":"2012-04-30T09:00:00Z",
 "decision_maker":"Kazuhiro Tsuga","tags":["portfolio_rationalization","tech_disruption"],
 "supported_by":["pan-e3","pan-e2","pan-e11"],
 "based_on":["panasonic-tesla-battery-partnership-2010"],
 "transitions":["trans-pan-2010-2012","trans-pan-2012-2014"]}
```

Headers include current X-Snapshot-ETag. All timestamps ISO-8601 UTC (Z).

```bash
curl -s http://localhost:8082/api/enrich/event/pan-e13 | jq .
```
Expect: An orphan event example (allowed): led_to: [] (or omitted).

```bash
curl -s http://localhost:8082/api/enrich/transition/trans-pan-2010-2012 | jq .
```
Expect: {"id":"trans-pan-2010-2012","from":"panasonic-tesla-battery-partnership-2010","to":"panasonic-exit-plasma-2012","relation":"causal","reason":"...","timestamp":"2013-10-09T00:00:00Z","tags":["strategic_pivot"]?} (tags optional per data).

### Text resolver

```bash
curl -s -X POST http://localhost:8082/api/resolve/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"Panasonic exits plasma TV production"}' | jq .
```
Expect: Top decision candidates with confidence; slug short-circuit supported by gateway.

### Graph expansion (k=1)

```bash
curl -s -X POST http://localhost:8082/api/graph/expand_candidates \
  -H 'Content-Type: application/json' \
  -d '{"node_id":"panasonic-exit-plasma-2012","k":1}' | jq .
```
Expect: All k=1 neighbors, unbounded collect: 3 events (pan-e2,pan-e3,pan-e11), 2 transitions (trans-pan-2010-2012,trans-pan-2012-2014). Gateway truncates only if prompt size exceeds limits.

## 5) Infrastructure

```bash
docker compose exec redis redis-cli ping
```
Expect: PONG.

```bash
curl -s -u root:batvault http://localhost:8529/_api/version | jq .
```
Expect: ArangoDB version JSON with server, version. Memory API readiness should depend on this.

```bash
docker compose exec api_edge curl -s -I http://minio:9000/minio/health/live
```
Expect: 200 OK.

```bash
curl -s -X POST http://localhost:8080/ops/minio/bucket | jq .
```
Expect: Same as the 8081 helper (see note above).

```bash
curl -s http://localhost:9090/-/ready
```
Expect: 200 OK from Prometheus.

```bash
curl -s http://localhost:3000/api/health | jq .
```
Expect: {"status":"ok"} (frontend service, if enabled). Milestones place this in M5+.

## 6) Headers & Audit artifacts

```bash
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | sed -n '1,40p'
```

Expect key headers:

- X-Request-ID: req_... (deterministic ID present in logs/artifacts)
- X-Snapshot-ETag: sha256:... (current corpus version)
- X-Policy-Id, X-Prompt-Id (optional header or present in body meta; spec requires in meta)
- Content-Type: application/json

Artifacts (envelope, rendered prompt, raw LLM JSON, validator report, final response) are written to S3/MinIO under the request ID. Note: spec references a "Replay endpoint" but does not define its path—flagging as a gap to document/implement.

## 7) Rate-limit sanity check

```bash
for i in $(seq 1 150); do curl -s http://localhost:8080/ratelimit-test >/dev/null; done && echo "burst done"

c=0; for i in $(seq 1 150); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ratelimit-test);
  if [ "$code" = "429" ]; then c=$((c+1)); fi
done; echo "429s: $c"
```

Expect: First loop completes; second loop prints a non-zero number of 429s, proving edge limiter is working. (Exact thresholds are deployment-config.)

## 8) Recommended additions (to fully test contracts & ops)

### A) Idempotency key (API Edge)

```bash
KEY=$(printf '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | sha256sum | cut -d' ' -f1)

curl -s -i -X POST http://localhost:8080/v2/ask \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: $KEY" \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}'
```

Expect: 200 with consistent X-Request-ID across retries for the same body+key (and single set of artifacts). Spec requires idempotency at API Edge.

### B) Schema-agnostic proof (new fields appear live)

```bash
curl -s http://localhost:8080/v2/schema/fields | jq '.based_on, .snippet, .tags, ."x-extra"'
```

Expect: Arrays present (aliases list), proving you don't need code changes to surface new fields.

### C) Orphan handling

```bash
curl -s http://localhost:8082/api/enrich/event/pan-e13 | jq '.led_to'
```

Expect: [] or field omitted; still 200. Completeness flags in answers should reflect missing neighbors when relevant.

### D) Allowed-IDs scope enforcement (validator)

Run /v2/ask and check:

```bash
curl -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | \
jq '.answer.supporting_ids as $s | .evidence.allowed_ids as $a | ($s - $a)'
```

Expect: [] (empty difference). If not empty, validator must fallback and set fallback_used:true.

### E) Caching sanity

Call /v2/ask twice and compare meta.latency_ms; the second should typically be lower due to resolver/evidence/LLM cache hits (ETag unchanged). Not a strict contract, but a good operational check.

### F) SSE headers

```bash
curl -i -sN -X POST 'http://localhost:8080/v2/query?stream=true' \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"text":"Why did Panasonic exit plasma TV production?"}' | sed -n '1,8p'
```

Expect headers: HTTP/1.1 200 OK, Content-Type: text/event-stream, Cache-Control: no-cache, plus X-Request-ID/X-Snapshot-ETag. Body should begin with id:/data: lines only after internal validation.

## What to watch in logs & metrics (quick checklist)

Each stage logs structured spans with deterministic IDs: request_id, prompt_fingerprint, bundle_fingerprint, snapshot_etag, policy_id, prompt_id. Selector logs selector_truncation and dropped IDs when applicable.

Metrics: TTFB, total latency, retries, fallback_used, cache hit rates. Alert on latency/error spikes.

## Small gaps / ambiguities I noticed

- Replay endpoint path is referenced but not specified; document/implement (e.g., /v2/replay/{request_id}) so operators can fetch stored artifacts via MinIO.
- Ops helper duplication: you have both :8081/ops/minio/ensure-bucket and :8080/ops/minio/bucket. Decide whether API Edge proxies these or standardize on the gateway path.
- SSE include_event=true "event names" aren't normative; the guarantee is validated short_answer only. If you want stable event names, codify them (e.g., chunk, done) in the contract.