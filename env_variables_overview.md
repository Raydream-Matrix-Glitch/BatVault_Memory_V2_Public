# Environment Variables Reference

## Core Configuration Variables

### **ENVIRONMENT**
- **Consuming code:** `core_config/settings.py` → **shared lib**
- **Purpose:** Sets the overall deployment mode (e.g. *dev*, *prod*). Controls debug switches and test data.

### **SERVICE_LOG_LEVEL**
- **Consuming code:** `core_config/settings.py`, `gateway/app.py` → **gateway**
- **Purpose:** How chatty the logs should be (DEBUG / INFO / WARN).

### **REQUEST_LOG_SAMPLE_RATE**
- **Consuming code:** `core_config/settings.py` → **shared lib**
- **Purpose:** Percentage of user requests that get full, detailed logging; lets you keep logs small while still observing behaviour.

### **AUTH_DISABLED**
- **Consuming code:** `core_config/settings.py` → **shared lib**
- **Purpose:** If *true*, all endpoints are open; no login/token required — handy for local dev.

## Performance Monitoring

### **PERF_ASK_P95_MS**
- **Consuming code:** `core_config/settings.py`
- **Purpose:** Target "95% of /ask calls should finish in ≤ 3000 ms". Only feeds the metrics dashboard.

### **PERF_QUERY_P95_MS**
- **Consuming code:** `core_config/settings.py`
- **Purpose:** Same as above but for `/query` requests.

## Database Configuration

### **ARANGO_URL**
- **Consuming code:** `ingest/cli.py`, `core_config/settings.py`
- **Purpose:** Where the graph database lives (hostname + port).

### **ARANGO_DB**
- **Consuming code:** `core_config/settings.py`
- **Purpose:** Which logical database to use inside that Arango instance.

### **ARANGO_ROOT_USER / ARANGO_ROOT_PASSWORD**
- **Consuming code:** `docker-compose.yml`, `core_config/settings.py` → **all Arango-aware services**
- **Purpose:** Credentials the containers use at start-up.

## Vector Search Configuration

### **ARANGO_VECTOR_INDEX_ENABLED**
- **Consuming code:** `core_storage/arangodb.py`, `ingest/vector_load.py`, others
- **Purpose:** Turns on experimental "semantic search" (vector index) features.

### **EMBEDDING_DIM**
- **Consuming code:** same files as above
- **Purpose:** The length of the AI vector for every text chunk (e.g. 768 numbers). Must match the model you're using.

### **VECTOR_METRIC**
- **Consuming code:** same
- **Purpose:** The mathematical distance rule (*cosine*, *l2*, …) the DB uses to find "nearest" vectors.

### **FAISS_NLISTS**
- **Consuming code:** `core_storage/arangodb.py`, `docker-compose.yml`
- **Purpose:** Advanced tuning knob for the Arango/FAISS vector index (bigger = faster search, slower load).

## Cache and Storage

### **REDIS_URL**
- **Consuming code:** `core_config/settings.py`
- **Purpose:** Where the in-memory cache / task queue lives.

### **CACHE_TTL_RESOLVER**, **CACHE_TTL_EVIDENCE**, **CACHE_TTL_LLM_JSON**, **CACHE_TTL_EXPAND**
- **Status:** **(unused as of M-3)**
- **Purpose:** Intended per-cache lifetimes; wiring still TODO.

### **MINIO_*** *(ENDPOINT / ACCESS_KEY / …)*
- **Consuming code:** `core_config/settings.py`, `core_storage/minio_client.py`
- **Purpose:** Where large artefacts (PDFs, images, etc.) are stored; MinIO looks and behaves like Amazon S3.

## Request Processing

### **MAX_PROMPT_BYTES**
- **Consuming code:** `gateway/app.py`, `core_config/constants.py`
- **Purpose:** Safety‐belt: trims user questions that exceed 8 kB before they hit the AI model.

### **SELECTOR_TRUNCATION_THRESHOLD**
- **Consuming code:** `gateway/selector.py`
- **Purpose:** When building answers from many snippets, keep making the bundle smaller until it's below this size.

### **MIN_EVIDENCE_ITEMS**
- **Consuming code:** `gateway/selector.py`
- **Purpose:** Never answer with fewer than *n* supporting facts.

## Feature Flags

### **ENABLE_SELECTOR_MODEL**, **ENABLE_LOAD_SHEDDING**, **ENABLE_GRAPH_EMBEDDINGS**, **ENABLE_ARTIFACT_RETENTION**, **ENABLE_CACHING**
- **Status:** **(unused as of M-3)**
- **Purpose:** Feature flags reserved for later roll-outs (selector AI model, auto-throttle, etc.).

## AI Model Configuration

### **LLM_MODE**
- **Consuming code:** `core_config/settings.py`
- **Purpose:** *off* = skip calling the expensive AI model; useful for CI.

### **ENABLE_EMBEDDINGS**
- **Consuming code:** `core_config/settings.py`, vector tests
- **Purpose:** Master flag that allows the code-path that generates vectors at all.

## Rate Limiting

### **API_RATE_LIMIT_DEFAULT**
- **Consuming code:** `gateway/app.py`, `api_edge/middleware.py`
- **Purpose:** Out-of-the-box "N requests per minute" throttle applied to every client unless they have special rules.

## Observability

### **OTEL_EXPORTER_OTLP_ENDPOINT**
- **Consuming code:** `docker-compose.yml` (env-pass-through)
- **Purpose:** Tells every service where to ship traces/metrics so they show up in Jaeger/Grafana dashboards.

---

## Key Observations

- **30 / 41 variables are actively consumed** right now. The eight marked "unused" are safe to leave in place; they simply have no effect until the corresponding feature work lands.

- Every live variable is **centralised through** `packages/core_config/settings.py`, which reads `os.getenv` and supplies defaults. Individual services import this shared settings module, so the mapping above covers all consumers.

- **Duplication note** – `.env` lists `ARANGO_VECTOR_INDEX_ENABLED` and `EMBEDDING_DIM` twice. That won't break anything (last one wins), but you may want to prune the earlier entries for clarity.