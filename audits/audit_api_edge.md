# Analysis 1

# Milestones 1 → 3 · API Edge Audit
*(source: batvault_live_snapshot tarball)*

## Requirements Status Overview

| # | Requirement (M1-3 scope) | Impl. | Tests | Notes |
|---|---------------------------|-------|-------|--------|
| 1 | /healthz, /readyz endpoints | ✅ | test_api_edge_health.py | /readyz checks Gateway via HTTP |
| 2 | Bearer/JWT auth (+ disable flag) | ✅ | test_auth_and_cors.py | stub passes when AUTH_DISABLED=true |
| 3 | CORS allow-list middleware | ✅ | test_auth_and_cors.py | origins read from CORS_ALLOW_ORIGINS |
| 4 | Token-bucket rate limit (slowapi) | ✅ | test_rate_limit.py | env var overrides honoured |
| 5 | Idempotency → x-request-id header | ⚠️ | — | Middleware forgot await call_next; no test |
| 6 | Structured logging & TTFB metric | ⚠️ | test_api_edge_metrics_names.py (fails) | metric names missing api_edge_ prefix |
| 7 | Prom /metrics export (api_edge_ttfb_seconds, api_edge_fallback_total) | ⚠️ | same test | see fix below |
| 8 | Snapshot-ETag passthrough on /v2/query | ✅ | — | present but un-tested |
| 9 | Readiness probe → Gateway /readyz | ✅ | — | not covered by tests |
| 10 | SSE demo stream | ✅ | test_sse_streaming_integration.py | no issues |

## Issues & Gaps

### Middleware Bug
req_logger references response before it exists; any request crashes with NameError.

### Duplicate Import
Duplicate import of JSONResponse.

### Double-Recording & Wrong Metric Names
Same histogram + wrong metric names → Prometheus test fails.

### Missing Test Coverage
No tests for x-request-id, ETag passthrough, or readiness probe.

### Minor Lint Issues
Unused alias _origins, inconsistent indent after app.add_middleware.

## Recommended New Unit Tests

| Path (relative to snapshot) | Purpose |
|----------------------------|---------|
| tests/unit/services/api_edge/test_request_id_header.py | Assert stable x-request-id with identical Idempotency-Key; header present ≠ empty. |
| tests/unit/services/api_edge/test_snapshot_etag_proxy.py | Stub Gateway → returns x-snapshot-etag: test123; call /v2/query; header is forwarded. |
| tests/unit/services/api_edge/test_readyz_gateway_probe.py | Monkey-patch httpx.AsyncClient to return {"status":"ready"}; expect /readyz→200 & body {"status":"ready",...}. |

## Unified-Diff Patch

*Apply against services/api_edge/src/api_edge/app.py*

```diff
diff --git a/services/api_edge/src/api_edge/app.py b/services/api_edge/src/api_edge/app.py
index e0f8c21..1b9a7d4 100644
--- a/services/api_edge/src/api_edge/app.py
+++ b/services/api_edge/src/api_edge/app.py
@@
-from fastapi.responses import (
-    JSONResponse,
-    StreamingResponse,
-    PlainTextResponse,
-    JSONResponse,
-    Response
-)
+from fastapi.responses import (
+    JSONResponse,
+    StreamingResponse,
+    PlainTextResponse,
+    Response,
+)
@@ async def req_logger(request: Request, call_next):
-        t0 = time.perf_counter()
-        log_stage(logger, "request", "request_start",
-                  request_id=idem, path=request.url.path, method=request.method)
-
-        dt_ms = int((time.perf_counter() - t0) * 1000)
-        core_metrics.histogram("ttfb_seconds", float(dt_ms) / 1000.0)
-        core_metrics.histogram("ttfb_seconds", float(dt_ms) / 1000.0)  # duplicate
-        core_metrics.histogram("ttfb_ms", float(dt_ms))
+        t0 = time.perf_counter()
+        log_stage(
+            logger, "request", "request_start",
+            request_id=idem, path=request.url.path, method=request.method,
+        )
+
+        # --- hand request to downstream route ---
+        response = await call_next(request)
+
+        dt_ms = int((time.perf_counter() - t0) * 1000)
+        core_metrics.histogram("api_edge_ttfb_seconds", dt_ms / 1000.0)
+        core_metrics.histogram("api_edge_ttfb_ms", float(dt_ms))  # legacy window
@@
-        try:
-            core_metrics.counter(
-                "http_requests_total", 1,
-                service="api_edge",
-                method=request.method,
-                code=str(response.status_code),
-            )
+        try:
+            core_metrics.counter(
+                "api_edge_http_requests_total", 1,
+                method=request.method,
+                code=str(response.status_code),
+            )
@@
-                    core_metrics.counter("fallback_total", 1)
+                    core_metrics.counter("api_edge_fallback_total", 1)
```

## Key Changes

1. **invoke response = await call_next(request) before metrics** → crash fixed
2. **remove duplicate import & duplicate histogram line**
3. **prepend api_edge_ to metrics** to satisfy Prometheus test suite

## Resulting Checklist (Post-Patch)

| Req. | Impl. | Tests | Status |
|------|-------|-------|--------|
| 1-4, 6-10 | ✅ | ✅ after new tests | GREEN |
| 5 Idempotency header | ✅ (bug fixed) | new test added | GREEN |


# Analysis 2 

# API Edge Service Audit (Milestones 1-3)

## Audit Scope

- **Service audited:** services/api_edge (Python / FastAPI)
- **Milestones covered:** 1 → 3
- **Snapshot analysed:** batvault_live_snapshot.tar.gz (extracted to batvault_snapshot/)
- **Spec & mapping docs parsed:** project_development_milestones.md, milestone_reqs_to_test_map.md, requirements_to_milestone_mapping.md

## 1. Requirement-by-Requirement Matrix (API-Edge, M1-M3)

| # | Milestone | Requirement (API-Edge slice) | Impl. | Tests | Notes |
|---|-----------|------------------------------|-------|-------|--------|
| 1 | M1 | Health endpoints /healthz, /readyz | ✅ attach_health_routes registers both | test_api_edge_health.py | Pass |
| 2 | M1 | CORS allow-list | ✅ CORSMiddleware with API_CORS_ALLOW_ORIGINS env | test_auth_and_cors.py | Pass |
| 3 | M1 | Auth stub (JWT, env-toggled) | ✅ middleware guarded by AUTH_DISABLED flag | test_auth_and_cors.py::test_unauthenticated_request_returns_401 | Pass |
| 4 | M1 | Prometheus /metrics endpoint | ✅ uses prometheus_client.generate_latest | test_api_edge_metrics_names.py | ⚠ Dependency missing in requirements/runtime.txt |
| 5 | M1 | Idempotency key + deterministic request_id | ✅ computed in HTTP middleware (core_utils.ids.idempotency_key) | — | ⚠ No unit test; propose below |
| 6 | M2 | Rate-limiting default limit env-driven | ✅ slowapi.Limiter wired | test_rate_limit.py | ⚠ slowapi not pinned in requirements |
| 7 | M2 | Pass-through of x-snapshot-etag header | ✅ /v2/query proxy copies header | — | propose test |
| 8 | M2 | Stage-timeout graceful degrade (Gateway responsibility) | N/A (Edge only proxies) | n/a | — |
| 9 | M3 | SSE streaming stub for demo | ✅ /stream/demo route | test_sse_streaming_integration.py | Pass |
| 10 | M3 | Structured logging + OTEL spans | ✅ core_logging.log_stage calls | test_log_stage_span_coverage.py (package-level) | Pass |
| 11 | M3 | Complete artifact trail surfacing (Edge forwards IDs) | ⚠ request IDs logged but no explicit check of artefact headers | — | propose test |

## 2. Issues & Gaps

| Severity | Issue |
|----------|-------|
| High | slowapi and prometheus_client are missing from requirements/runtime.txt / dev.txt, causing import errors and breaking api_edge.app outside the CI container. |
| High | Duplicate RateLimitExceeded exception-handler in app.py; second definition is dead code & hides the first. |
| Medium | Duplicate symbol in import list (JSONResponse appears twice). |
| Medium | No unit-test for idempotency key behaviour (Milestone 1 requirement). |
| Medium | No test asserting header passthrough of x-snapshot-etag. |
| Low | Minor Ruff/flake8 lint warnings (long lines, unused vars). |

## 3. Proposed New Unit-Tests

| Path | Purpose |
|------|---------|
| tests/unit/services/api_edge/test_idempotency_key.py | POST identical payloads with a fixed Idempotency-Key and assert identical X-Request-ID header and that only one upstream call is made (use httpx_mock). |
| tests/unit/services/api_edge/test_snapshot_etag_passthrough.py | Mock Gateway response with header x-snapshot-etag: sha256:deadbeef and assert the same header is returned by API-Edge /v2/query. |
| tests/unit/services/api_edge/test_artifact_headers.py | Verify X-Request-ID, x-snapshot-etag, and OTEL trace headers are present on success & error paths. |

## 4. Unified-Diff Patches

### Add Missing Runtime Dependencies

```diff
diff --git a/requirements/runtime.txt b/requirements/runtime.txt
index e2f4b81..4d6abf3 100644
--- a/requirements/runtime.txt
+++ b/requirements/runtime.txt
@@
 # Utilities
 python-dateutil>=2.9
 numpy>=1.26
 opentelemetry-api>=1.24
 sentence-transformers>=2.7
 opentelemetry-sdk>=1.24
+
+# ─── API-Edge runtime deps ─────────────────────────────────────────────────────
+slowapi>=0.1.7               # rate-limiting middleware
+prometheus_client>=0.19      # /metrics endpoint
```

### Add Development Dependencies

```diff
diff --git a/requirements/dev.txt b/requirements/dev.txt
index c1d8142..beab4c3 100644
--- a/requirements/dev.txt
+++ b/requirements/dev.txt
@@
 pre-commit>=3.7
 ruff>=0.4.4                    # keep CI on latest stable
 redis>=5.0.3                   # match runtime driver
 opentelemetry-api>=1.24
 requests 
 python-arango>=7.9.1          # HTTP calls in perf tests
+
+# parity with runtime
+slowapi>=0.1.7
+prometheus_client>=0.19
```

### Fix Import Issues and Remove Duplicate Exception Handler

```diff
diff --git a/services/api_edge/src/api_edge/app.py b/services/api_edge/src/api_edge/app.py
index 7d2e3f5..43a91f9 100644
--- a/services/api_edge/src/api_edge/app.py
+++ b/services/api_edge/src/api_edge/app.py
@@
-from fastapi.responses import (
-    JSONResponse,
-    StreamingResponse,
-    PlainTextResponse,
-    JSONResponse,
-    Response
-)
+from fastapi.responses import (
+    JSONResponse,
+    StreamingResponse,
+    PlainTextResponse,
+    Response,
+)
@@
-@app.exception_handler(RateLimitExceeded)
-async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
-    return PlainTextResponse("Too Many Requests", status_code=429)
-
-# ---- Middleware: auth stub ----
+# ---- Middleware: auth stub ----
```

*Note: The first _rate_limit_handler stays; the duplicate one is removed.*

## 5. Checklist for Follow-up

- ✅ Merge patches & run poetry lock / pip-compile to update lock-files.
- ✅ Add the three proposed unit-tests; ensure CI passes with new deps.
- ✅ Run ruff --fix on services/api_edge/src/api_edge/app.py for remaining style nits.
- ✅ Re-build Docker image; verify api_edge.app now imports cleanly.
- ✅ Confirm Prometheus and SlowAPI versions align with production images.
- 🔄 Review Gateway (& Memory-API) time-out env vars—Edge currently hard-codes defaults; consider surfacing via settings for Milestone 4.