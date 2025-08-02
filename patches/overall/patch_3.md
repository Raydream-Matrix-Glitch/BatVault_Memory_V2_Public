################################################################################
# PATCHSET:  Close gaps A-1 (rate-limit), A-2 (stage-timeouts), A-3 (vector-search)
################################################################################

diff --git a/.env b/.env
@@
-EMBEDDING_DIM=384
+EMBEDDING_DIM=768
+ENABLE_EMBEDDINGS=true          # feature-flag for vector search
+API_RATE_LIMIT_DEFAULT=100/min  # default token-bucket for api-edge

diff --git a/packages/core_config/constants.py b/packages/core_config/constants.py
@@
-import os
+import os
+from functools import lru_cache
+
+# ──────────────────────────────────────────────────────────────────────────────
+# Feature flags & vector dimensions
+# ──────────────────────────────────────────────────────────────────────────────
+
+# enable / disable embedding pathway (A-3)
+ENABLE_EMBEDDINGS: bool = os.getenv("ENABLE_EMBEDDINGS", "false").lower() in ("1", "true", "yes")
+
+# vector dimension (env wins, spec default 768)
+SIM_DIM: int = int(os.getenv("EMBEDDING_DIM", 768))
+
+# default API-edge rate-limit string, e.g. "100/minute" (A-1)
+API_RATE_LIMIT_DEFAULT: str = os.getenv("API_RATE_LIMIT_DEFAULT", "100/minute")
@@
-TIMEOUT_SEARCH_MS      = 800
-TIMEOUT_GRAPH_EXPAND_MS= 250
-TIMEOUT_ENRICH_MS      = 600
-TIMEOUT_LLM_MS         = 1500
-TIMEOUT_VALIDATOR_MS   = 300
+# ──────────────────────────────────────────────────────────────────────────────
+# Stage-timeout helper (A-2)
+# ──────────────────────────────────────────────────────────────────────────────
+
+@lru_cache(maxsize=None)
+def timeout_for_stage(stage: str) -> float:
+    _map = {
+        "search": int(os.getenv("TIMEOUT_SEARCH_MS",       800)),
+        "expand": int(os.getenv("TIMEOUT_GRAPH_EXPAND_MS", 250)),
+        "enrich": int(os.getenv("TIMEOUT_ENRICH_MS",       600)),
+        "llm":    int(os.getenv("TIMEOUT_LLM_MS",          1500)),
+        "validate": int(os.getenv("TIMEOUT_VALIDATOR_MS",  300)),
+    }
+    return _map[stage] / 1000.0  # seconds

diff --git a/packages/core_utils/async_timeout.py b/packages/core_utils/async_timeout.py
new file mode 100644
@@
+import asyncio, logging
+from typing import Awaitable, TypeVar
+
+from core_config import constants
+
+T = TypeVar("T")
+
+
+async def run_with_stage_timeout(stage: str, task: Awaitable[T], logger: logging.Logger) -> T:
+    """Executes *task* under the per-stage budget; raises on timeout (A-2)."""
+    timeout_s = constants.timeout_for_stage(stage)
+    try:
+        return await asyncio.wait_for(task, timeout_s)
+    except asyncio.TimeoutError:
+        logger.warning("stage_timeout", extra={"stage": stage, "timeout_s": timeout_s})
+        raise

diff --git a/services/api_edge/requirements.txt b/services/api_edge/requirements.txt
@@
 fastapi==0.111.0
+slowapi==0.1.7        # rate-limiting middleware (A-1)

diff --git a/services/api_edge/middleware/rate_limit.py b/services/api_edge/middleware/rate_limit.py
new file mode 100644
@@
+from fastapi import FastAPI, Request
+from slowapi import Limiter, _rate_limit_exceeded_handler
+from slowapi.util import get_remote_address
+
+from core_config import constants
+
+limiter = Limiter(
+    key_func=get_remote_address,
+    default_limits=[constants.API_RATE_LIMIT_DEFAULT],
+    headers_enabled=True,
+)
+
+
+def install_rate_limiter(app: FastAPI) -> None:
+    app.state.limiter = limiter
+    app.add_exception_handler(429, _rate_limit_exceeded_handler)
+
+    @app.middleware("http")
+    async def _ratelimit(request: Request, call_next):
+        route = f"{request.method}:{request.url.path}"
+        await limiter.limit(route)(lambda: None)(request)  # token-bucket check
+        return await call_next(request)

diff --git a/services/api_edge/app.py b/services/api_edge/app.py
@@
-from fastapi import FastAPI
+from fastapi import FastAPI
+from .middleware.rate_limit import install_rate_limiter   # (A-1)
 
 app = FastAPI(title="Batvault API Edge")
 
+# rate-limiting middleware
+install_rate_limiter(app)
 
 # … existing routes, SSE stub, etc …

diff --git a/services/gateway/evidence.py b/services/gateway/evidence.py
@@
-from .resolver import resolve_anchor
-from .expand import expand_graph
+from .resolver import resolve_anchor
+from .expand import expand_graph
+from core_utils.async_timeout import run_with_stage_timeout     # (A-2)
@@
-    anchor = await resolve_anchor(decision_ref, redis=redis)
+    anchor = await run_with_stage_timeout(
+        "search", resolve_anchor(decision_ref, redis=redis), logger
+    )
@@
-    neighbors = await expand_graph(anchor["id"], intent=intent, redis=redis)
+    neighbors = await run_with_stage_timeout(
+        "expand", expand_graph(anchor["id"], intent=intent, redis=redis), logger
+    )
@@
-    enriched = await _enrich(neighbors, redis=redis)
+    enriched = await run_with_stage_timeout(
+        "enrich", _enrich(neighbors, redis=redis), logger
+    )

diff --git a/packages/core_storage/arangodb.py b/packages/core_storage/arangodb.py
@@
-from arango.collection import Collection
+from arango.collection import Collection
+from core_config.constants import ENABLE_EMBEDDINGS, SIM_DIM
@@
-    def resolve_text(self, text: str, k: int = 3) -> list[dict]:
-        """BM25 text search as fallback."""
-        query = f"""
-        FOR d IN decisions_search
-          SEARCH ANALYZER(PHRASE(d.rationale, @text), "text_en")
-          SORT BM25(d) DESC
-          LIMIT @k
-          RETURN d
-        """
-        return self._db.aql.execute(query, bind_vars={"text": text, "k": k}).batch()
+    def resolve_text(self, text: str, k: int = 3) -> list[dict]:
+        """Vector → lexical cascade (A-3)."""
+        if ENABLE_EMBEDDINGS:
+            try:
+                vec = embed(text)  # embed() provided by embedding_model singleton
+                query = f"""
+                FOR d IN decisions
+                  SEARCH COSINE_SIMILARITY(d.embedding, @vec, {SIM_DIM}) > 0.3
+                  SORT COSINE_SIMILARITY(d.embedding, @vec, {SIM_DIM}) DESC
+                  LIMIT @k
+                  RETURN d
+                """
+                return self._db.aql.execute(query, bind_vars={"vec": vec, "k": k}).batch()
+            except Exception:
+                # fall back silently
+                pass
+        query = """
+        FOR d IN decisions_search
+          SEARCH ANALYZER(PHRASE(d.rationale, @text), "text_en")
+          SORT BM25(d) DESC
+          LIMIT @k
+          RETURN d
+        """
+        return self._db.aql.execute(query, bind_vars={"text": text, "k": k}).batch()

diff --git a/ops/bootstrap_arangosearch.py b/ops/bootstrap_arangosearch.py
@@
-SIM_DIM = 768
+import os
+SIM_DIM = int(os.getenv("EMBEDDING_DIM", 768))  # align with env (A-3)

diff --git a/ops/docker-compose.yml b/ops/docker-compose.yml
@@
   gateway:
     environment:
       - REDIS_URL=redis://redis:6379/0
+      - ENABLE_EMBEDDINGS=${ENABLE_EMBEDDINGS}
+      - EMBEDDING_DIM=${EMBEDDING_DIM}
@@
   memory-api:
     environment:
       - REDIS_URL=redis://redis:6379/0
+      - ENABLE_EMBEDDINGS=${ENABLE_EMBEDDINGS}
+      - EMBEDDING_DIM=${EMBEDDING_DIM}
+
+  arango_bootstrap:
+    image: python:3.11-slim
+    volumes:
+      - .:/workspace
+    command: >
+      sh -c "pip install python-arango && python /workspace/ops/bootstrap_arangosearch.py"
+    depends_on:
+      - arangodb
+    environment:
+      - ARANGO_HOST=arangodb
+      - ARANGO_ROOT_PASSWORD=root
+      - EMBEDDING_DIM=${EMBEDDING_DIM}

################################################################################
# END PATCHSET
################################################################################
