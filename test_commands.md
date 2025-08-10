# BatVault Memory V2 - Test Commands

## 1) Health / Ready / Metrics

### API Edge

```bash
curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/readyz
curl -s http://localhost:8080/metrics | head -n 20
```

### Gateway

```bash
curl -s http://localhost:8081/healthz || echo "Gateway exposes readiness via API Edge proxy only"
curl -s http://localhost:8081/metrics | head -n 20
```

### Memory API

```bash
curl -s http://localhost:8082/healthz
curl -s http://localhost:8082/metrics | head -n 20
```

### Ingest

```bash
curl -s http://localhost:8083/healthz
curl -s http://localhost:8083/metrics | head -n 20
```

## 2) API Contracts — /v2 via API Edge

### Structured /v2/ask (JSON)

```bash
curl -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | jq .
```

### Structured /v2/ask (SSE streaming)

```bash
curl -sN -X POST 'http://localhost:8080/v2/ask?stream=true' \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}'
```

### Structured /v2/ask (SSE + event framing)

```bash
curl -sN -X POST 'http://localhost:8080/v2/ask?stream=true&include_event=true' \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}'
```

### Natural-language /v2/query (JSON)

```bash
curl -s -X POST http://localhost:8080/v2/query \
  -H 'Content-Type: application/json' \
  -d '{"text":"Why did Panasonic exit plasma TV production?"}' | jq .
```

### Natural-language /v2/query (SSE streaming)

```bash
curl -sN -X POST 'http://localhost:8080/v2/query?stream=true' \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"text":"Why did Panasonic exit plasma TV production?"}'
```

### Schema

```bash
curl -s http://localhost:8080/v2/schema/fields | jq .
curl -s http://localhost:8080/v2/schema/rels | jq .
```

### Error-surfacing probes

```bash
# Missing anchor_id/evidence
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision"}'

# Unknown decision slug
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"non-existent-decision-xyz"}'
```

## 3) Gateway internal helpers

```bash
curl -s -X POST http://localhost:8081/ops/minio/ensure-bucket | jq .
curl -s http://localhost:8081/evidence/panasonic-exit-plasma-2012 | jq .
```

From inside API Edge container:

```bash
docker compose exec api_edge curl -s http://gateway:8081/ops/minio/ensure-bucket | jq .
docker compose exec api_edge curl -s http://gateway:8081/evidence/panasonic-exit-plasma-2012 | jq .
```

## 4) Memory API

```bash
# Schema
curl -s http://localhost:8082/api/schema/fields | jq .
curl -s http://localhost:8082/api/schema/rels | jq .
curl -s http://localhost:8082/api/schema/relations | jq .

# Enrichment
curl -s http://localhost:8082/api/enrich/decision/panasonic-exit-plasma-2012 | jq .
curl -s http://localhost:8082/api/enrich/event/panasonic-exit-plasma-2012 | jq .
curl -s http://localhost:8082/api/enrich/transition/panasonic-exit-plasma-2012 | jq .

# Text Resolver
curl -s -X POST http://localhost:8082/api/resolve/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"Panasonic exits plasma TV production"}' | jq .

# Graph expansion
curl -s -X POST http://localhost:8082/api/graph/expand_candidates \
  -H 'Content-Type: application/json' \
  -d '{"node_id":"panasonic-exit-plasma-2012","k":1}' | jq .
```

## 5) Infrastructure

```bash
docker compose exec redis redis-cli ping
curl -s -u root:batvault http://localhost:8529/_api/version | jq .
docker compose exec api_edge curl -s -I http://minio:9000/minio/health/live
curl -s -X POST http://localhost:8080/ops/minio/bucket | jq .
curl -s http://localhost:9090/-/ready
curl -s http://localhost:3000/api/health | jq .
# Jaeger UI: http://localhost:16686
```

## 6) Headers & Audit Artifacts

```bash
curl -i -s -X POST http://localhost:8080/v2/ask \
  -H 'Content-Type: application/json' \
  -d '{"intent":"why_decision","anchor_id":"panasonic-exit-plasma-2012"}' | sed -n '1,20p'
```

## 7) Rate-limit sanity check

```bash
for i in $(seq 1 150); do \
  curl -s http://localhost:8080/ratelimit-test >/dev/null; \
done && echo "burst done"

c=0; for i in $(seq 1 150); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ratelimit-test);
  if [ "$code" = "429" ]; then c=$((c+1)); fi
done; echo "429s: $c"
```