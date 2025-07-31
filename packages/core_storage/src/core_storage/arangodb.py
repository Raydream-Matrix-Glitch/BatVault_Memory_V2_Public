from typing import Any, Dict, List, Optional, Tuple
import os, re, hashlib, logging
import httpx
import json
from functools import cached_property
from pydantic import BaseModel
from core_config import get_settings
from core_logging import get_logger, log_stage

logger = get_logger("core_storage")

class ArangoStore:
    def __init__(self,
                 url: str,
                 root_user: str,
                 root_password: str,
                 db_name: str,
                 graph_name: str = "batvault_graph",
                 catalog_col: str = "catalog",
                 meta_col: str = "meta",
                 *,
                 lazy: bool = True):
        """
        *lazy=True* prevents hard failures in CI and unit tests when the
        ArangoDB container is not running.  A real connection is established
        only on the first call that actually needs it.
        """
        self._url, self._root_user, self._root_password = url, root_user, root_password
        self._db_name, self._graph_name = db_name, graph_name
        self.catalog_col, self.meta_col = catalog_col, meta_col
        self.db: Optional[object]    = None   # filled by _connect()
        self.graph: Optional[object] = None
        if not lazy:
            self._connect()

    # -------------------------------------------------- #
    # Lazy connection helper                             #
    # -------------------------------------------------- #
    def _connect(self) -> None:
        if self.db is not None:           # already connected or stubbed
            return
        try:
            from arango import ArangoClient              # local import
            client = ArangoClient(hosts=self._url)
            sys_db = client.db("_system",
                               username=self._root_user,
                               password=self._root_password)
            if not sys_db.has_database(self._db_name):
                sys_db.create_database(self._db_name)
            self.db    = client.db(self._db_name,
                                   username=self._root_user,
                                   password=self._root_password)
            self.graph = self.db.graph(self._graph_name) if self.db.has_graph(self._graph_name) \
                         else self.db.create_graph(self._graph_name)
        except Exception as exc:
            # Stub-mode: keep attributes None so unit tests can monkey-patch
            logger.warning("ArangoDB unavailable – running in stub mode (%s)", exc)
            self.db = self.graph = None
            return                # ⇠  **early-out** – stay in stub mode

        # Guard – nothing to initial­ise when the DB isn’t reachable
        if self.db is None:
            return

        # Ensure collections
        for c in ("nodes", "edges", self.catalog_col, self.meta_col):
            if not self.db.has_collection(c):
                if c == "edges":
                    self.db.create_collection(c, edge=True)
                else:
                    self.db.create_collection(c)

        # Ensure graph
        if not self.db.has_graph(self._graph_name):
            self.db.create_graph(self._graph_name)
        self.graph = self.db.graph(self._graph_name)
        # Edge definition: edges between nodes (single super-edge collection)
        if self.graph and not self.graph.has_edge_definition("edges"):
            self.graph.create_edge_definition(
                edge_collection="edges",
                from_vertex_collections=["nodes"],
                to_vertex_collections=["nodes"],
            )
        # ------------------------------------------------------------------
        #  Optional: create HNSW vector index on `nodes.embedding`
        #  (guarded by env ARANGO_VECTOR_INDEX_ENABLED=true)
        # ------------------------------------------------------------------
        if os.getenv("ARANGO_VECTOR_INDEX_ENABLED", "false").lower() == "true":
            self._maybe_create_vector_index()

    def _count_vectors(self) -> int:
        """Count docs that actually have an embedding field."""
        try:
            cursor = self.db.aql.execute(
                'RETURN LENGTH(FOR d IN nodes FILTER HAS(d, "embedding") RETURN 1)'
            )
            return int(next(cursor))
        except Exception as exc:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_estimate_warn", error=str(exc))
            return 0

    def _maybe_create_vector_index(self) -> None:
        """Create FAISS IVF index once there are enough training vectors."""
        cfg = get_settings()
        url = f"{cfg.arango_url}/_db/{self.db.name}/_api/index"
        params = {"collection": "nodes"}

        vector_count = self._count_vectors()
        desired_nlists = int(os.getenv("FAISS_NLISTS", 100))
        # heuristic: sqrt(N), bounded to desired_nlists and >=1
        from math import sqrt, floor
        effective_nlists = max(1, min(desired_nlists, floor(sqrt(vector_count)) if vector_count else 0))

        if vector_count == 0:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_deferred",
                      reason="no_vectors", collection="nodes",
                      desired_nlists=desired_nlists, vector_count=vector_count)
            return

        payload = {
            "type": "vector",
            "name": "idx_nodes_embedding",
            "fields": ["embedding"],
            "inBackground": True,
            "params": {
                "dimension": int(os.getenv("EMBEDDING_DIM", 384)),
                "metric": os.getenv("VECTOR_METRIC", "cosine"),   # cosine | l2
                "nLists": effective_nlists,                       # IVF cluster count
            },
        }

        user, pwd = cfg.arango_root_user, cfg.arango_root_password
        auth = httpx.BasicAuth(user, pwd) if (user and pwd) else None

        try:
            resp = httpx.post(url, params=params, json=payload, auth=auth, timeout=10.0)
            common = {
                "status": resp.status_code,
                "index_name": payload["name"],
                "collection": "nodes",
                "dimension": payload["params"]["dimension"],
                "metric": payload["params"]["metric"],
                "nLists": payload["params"]["nLists"],
                "vector_count": vector_count,
                "desired_nlists": desired_nlists,
                "effective_nlists": effective_nlists,
            }
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_params", **common)
            if resp.status_code in (200, 201):
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_created", **common)
            elif resp.status_code == 409 or (resp.headers.get("content-type","").startswith("application/json") and resp.json().get("errorNum") == 1210):
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_exists", **common)
            else:
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_warn",
                          body=resp.text[:500], **common, payload_schema="vector+params/faiss")
        except Exception as exc:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_error", error=str(exc))

    # ----------------- Upserts -----------------
    def upsert_node(self, node_id: str, node_type: str, payload: Dict[str, Any]) -> None:
        doc = dict(payload)
        doc["_key"] = node_id
        doc["type"] = node_type
        col = self.db.collection("nodes")
        col.insert(doc, overwrite=True)

    def upsert_edge(
        self,
        edge_id: str,
        from_id: str,
        to_id: str,
        rel_type: str,
        payload: Dict[str, Any],
    ) -> None:
        # ------------------------------------------------------------------
        #  Sanitise the edge _key so it complies with Arango’s
        #  `^[A‑Za‑z0‑9_:\.\-]+$` regex and ≤ 254 bytes.
        # ------------------------------------------------------------------
        safe_key = self._safe_key(edge_id)
        if safe_key != edge_id:  # strategic structured log for auditing
            logger.info(
                "edge_key_sanitised",
                extra={
                    "raw": edge_id,
                    "sanitised": safe_key,
                    "stage": "storage",
                },
            )

        doc = dict(payload)
        doc["_key"] = safe_key
        doc["_from"] = f"nodes/{from_id}"
        doc["_to"] = f"nodes/{to_id}"
        doc["type"] = rel_type
        self.db.collection("edges").insert(doc, overwrite=True)

    # ---------------------------------------------------------------
    #  helpers
    # ---------------------------------------------------------------

    _ILLEGAL_CHARS = re.compile(r"[^A-Za-z0-9_\-:\.]")

    def _safe_key(self, raw: str) -> str:
        """
        • Replace every illegal char with “_”.
        • If the result exceeds 254 bytes, truncate and append an 8‑char hash
          so the transformation stays deterministic.
        """
        cleaned = self._ILLEGAL_CHARS.sub("_", raw)
        if len(cleaned.encode()) <= 254:
            return cleaned
        digest = hashlib.sha1(cleaned.encode()).hexdigest()[:8]
        return f"{cleaned[:245]}_{digest}"

    # ----------------- Catalogs -----------------
    def set_field_catalog(self, catalog: Dict[str, List[str]]) -> None:
        self.db.collection(self.catalog_col).insert(
            {"_key": "fields", "fields": catalog}, overwrite=True
        )

    def set_relation_catalog(self, relations: List[str]) -> None:
        self.db.collection(self.catalog_col).insert(
            {"_key": "relations", "relations": relations}, overwrite=True
        )

    def get_field_catalog(self) -> Dict[str, List[str]]:
        doc = self.db.collection(self.catalog_col).get("fields") or {"fields": {}}
        return doc["fields"]

    def get_relation_catalog(self) -> List[str]:
        doc = self.db.collection(self.catalog_col).get("relations") or {"relations": []}
        return doc["relations"]

    # ----------------- Snapshot meta -----------------
    def set_snapshot_etag(self, etag: str) -> None:
        self.db.collection(self.meta_col).insert({"_key": "snapshot", "etag": etag}, overwrite=True)

    def get_snapshot_etag(self) -> Optional[str]:
        if self.db is None:
            self._connect()
        if self.db is None:
            return ""
        doc = self.db.collection(self.meta_col).get("snapshot")
        return doc.get("etag") if doc else None
    # ------------------------------------------------------------------
    #  Snapshot GC – drop anything whose stamp ≠ the current batch
    # ------------------------------------------------------------------
    def prune_stale(self, snapshot_etag: str) -> Tuple[int, int]:
        """
        Delete nodes **and** edges whose ``snapshot_etag`` is missing
        or different from the stamp supplied.  
        Returns ``(nodes_removed, edges_removed)`` for audit-logs.
        """
        # Nodes ----------------------------------------------------------
        nodes_removed = int(
            next(
                self.db.aql.execute(
                    """
                    RETURN LENGTH(
                      FOR d IN nodes
                        FILTER !HAS(d,'snapshot_etag') || d.snapshot_etag != @etag
                        RETURN 1
                    )""",
                    bind_vars={"etag": snapshot_etag},
                )
            )
        )
        self.db.aql.execute(
            """
            FOR d IN nodes
              FILTER !HAS(d,'snapshot_etag') || d.snapshot_etag != @etag
              REMOVE d IN nodes
            """,
            bind_vars={"etag": snapshot_etag},
        )

        # Edges ----------------------------------------------------------
        edges_removed = int(
            next(
                self.db.aql.execute(
                    """
                    RETURN LENGTH(
                      FOR e IN edges
                        FILTER !HAS(e,'snapshot_etag') || e.snapshot_etag != @etag
                        RETURN 1
                    )""",
                    bind_vars={"etag": snapshot_etag},
                )
            )
        )
        self.db.aql.execute(
            """
            FOR e IN edges
              FILTER !HAS(e,'snapshot_etag') || e.snapshot_etag != @etag
              REMOVE e IN edges
            """,
            bind_vars={"etag": snapshot_etag},
        )

        return nodes_removed, edges_removed

    # ----------------- Enrichment (envelopes) -----------------
    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.db.collection("nodes").get(node_id)

    def get_enriched_decision(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self.get_node(node_id)
        if not n or n.get("type") != "decision":
            return None
        return {
            "id": n["_key"],
            "option": n.get("option"),
            "rationale": n.get("rationale"),
            "timestamp": n.get("timestamp"),
            "decision_maker": n.get("decision_maker"),
            "tags": n.get("tags", []),
            "supported_by": n.get("supported_by", []),
            "based_on": n.get("based_on", []),
            "transitions": n.get("transitions", []),
        }

    def get_enriched_event(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self.get_node(node_id)
        if not n or n.get("type") != "event":
            return None
        # summary repair already handled in ingest/normalize; serve stored
        return {
            "id": n["_key"],
            "summary": n.get("summary"),
            "description": n.get("description"),
            "timestamp": n.get("timestamp"),
            "tags": n.get("tags", []),
            "led_to": n.get("led_to", []),
            "snippet": n.get("snippet"),
        }

    def get_enriched_transition(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self.get_node(node_id)
        if not n or n.get("type") != "transition":
            return None
        return {
            "id": n["_key"],
            "from": n.get("from"),
            "to": n.get("to"),
            "relation": n.get("relation"),
            "reason": n.get("reason"),
            "timestamp": n.get("timestamp"),
            "tags": n.get("tags", []),
        }

    # ----------------- Resolver & Graph Expansion -----------------
    def _redis(self):
        try:
            import redis  # type: ignore
        except Exception:
            return None
        try:
            return redis.Redis.from_url(get_settings().redis_url)  # type: ignore
        except Exception:
            return None

    def _cache_get(self, key: str):
        r = self._redis()
        if not r:
            return None
        try:
            v = r.get(key)
            if v:
                import orjson
                return orjson.loads(v)
        except Exception:
            return None
        return None

    def _cache_set(self, key: str, value, ttl: int):
        r = self._redis()
        if not r:
            return
        try:
            import orjson
            r.setex(key, ttl, orjson.dumps(value))
        except Exception:
            return

    def expand_candidates(self, anchor_id: str, k: int = 1) -> dict:
        """
        Return k‑hop neighbors around an anchor node. k is clamped to 1 per M2.
        """
        # Allow unit tests to inject a dummy `.db` before we ever touch Arango.
        if self.db is None:
            self._connect()
        if self.db is None:            # still None → stub response (CI path)
            return {
                "anchor": anchor_id,
                "neighbors": [],
                "meta": {"snapshot_etag": ""},
            }

        k = 1
        cache_key = f"expand:{anchor_id}:k{str(k)}"
        cached = self._cache_get(cache_key)
        # ------------------------------------------------------------------ #
        # Contract: every resolver response **must** echo the original query
        # string.  Older cache entries (written before this rule) may be
        # missing it, so we patch them in on read.                           #
        # ------------------------------------------------------------------ #
        if cached:
            # ── Contract repair for legacy cache entries ───────────────────────
            # • `neighbors` must be a *list* (older code stored {events,transitions})
            # • `anchor` must always be present
            if isinstance(cached.get("neighbors"), dict):
                cached = {
                    **cached,
                    "neighbors": (cached["neighbors"].get("events") or [])
                                 + (cached["neighbors"].get("transitions") or [])
                }
            if "anchor" not in cached:
                cached = {**cached, "anchor": anchor_id}
            return cached

        aql = """
        LET anchor = DOCUMENT('nodes', @anchor)
        LET outgoing = (FOR v,e IN 1..1 OUTBOUND anchor GRAPH @graph RETURN {node: v, edge: e})
        LET incoming = (FOR v,e IN 1..1 INBOUND  anchor GRAPH @graph RETURN {node: v, edge: e})
        RETURN { anchor: anchor, neighbors: APPEND(outgoing, incoming) }
        """
        cursor = self.db.aql.execute(aql, bind_vars={"anchor": anchor_id,
                                                    "graph":  self._graph_name})
        doc = next(cursor, {"anchor": None, "neighbors": []})
        result = {
            "anchor": doc.get("anchor"),
            "neighbors": [
                {
                    "id": n["node"].get("_key"),
                    "type": n["node"].get("type"),
                    "title": n["node"].get("title"),
                    "edge": {
                        "relation": n["edge"].get("relation"),
                        "timestamp": n["edge"].get("timestamp"),
                    },
                }
                for n in doc.get("neighbors", [])
                if n.get("node") and n.get("edge")
            ],
        }
        self._cache_set(cache_key, result, get_settings().cache_ttl_expand_sec)
        return result

    def resolve_text(self, q: str, limit: int = 10, use_vector: bool = False, query_vector: list[float] | None = None) -> dict:
        """
        BM25/TFIDF text resolve via ArangoSearch view if available; fallback to LIKE scan.
        Returns [{id, score, title, type}].
        """
        key = f"resolve:{hash((q, bool(use_vector)))}:l{limit}"
        cached = self._cache_get(key)
        if cached:
            # ── Milestone-2: ensure every cached hit meets the resolver contract ──
            if "query" not in cached or cached["query"] is None:
                cached = {**cached, "query": q}
            cached.setdefault("matches", [])
            cached.setdefault("vector_used", False)
            cached.setdefault("resolved_id", q)
            cached.setdefault("meta", {})
            return cached
        results = []
        if self.db is None:
            self._connect()
        if self.db is None:
            # CI / unit-test stub. Must satisfy Milestone-2 contract in **full**.
            return {
                "query": q,
                "matches": [],
                "vector_used": bool(use_vector),
                "resolved_id": q,                # 🔑 always non-null
                "meta": {"snapshot_etag": ""},    # keep headers deterministic
            }
        settings = get_settings()
        # Optional vector search branch
        if use_vector and settings.enable_embeddings:
            vector_idx_enabled = os.getenv('ARANGO_VECTOR_INDEX_ENABLED','false').lower() == 'true'
            if vector_idx_enabled:
                try:
                    if not query_vector:
                        raise Exception('query_vector required when use_vector=true')
                    aql = (
                        "FOR d IN nodes FILTER HAS(d,'embedding') "
                        "LET score = APPROX_NEAR_COSINE(d.embedding, @qv) "
                        "SORT score DESC LIMIT @limit "
                        "RETURN {id: d._key, score: score, title: d.title, type: d.type}"
                    )
                    cursor = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                    results = list(cursor)
                    resp = {"query": q, "matches": results, "vector_used": True}
                    self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
                    return resp
                except Exception:
                    # fall through to brute-force or BM25/LIKE
                    pass
            if use_vector and not vector_idx_enabled:
                try:
                    aql = (
                        "FOR d IN nodes FILTER HAS(d,'embedding') "
                        "LET score = COSINE_SIMILARITY(d.embedding, @qv) "
                        "SORT score DESC LIMIT @limit "
                        "RETURN {id: d._key, score: score, title: d.title, type: d.type}"
                    )
                    cursor = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                    results = list(cursor)
                    resp = {"query": q, "matches": results, "vector_used": True}
                    self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
                    return resp
                except Exception:
                    pass
        try:
            aql = (
                "FOR d IN nodes_search "
                "SEARCH ANALYZER( PHRASE(d.rationale, @q) OR PHRASE(d.description, @q) "
                "OR PHRASE(d.reason, @q) OR PHRASE(d.summary, @q), 'text_en' ) "
                "SORT BM25(d) DESC LIMIT @limit "
                "RETURN {id: d._key, score: BM25(d), title: d.title, type: d.type}"
            )
            cursor = self.db.aql.execute(aql, bind_vars={"q": q, "limit": limit})
            results = list(cursor)
        except Exception:
            aql = (
                "FOR d IN nodes "
                "FILTER LIKE(LOWER(d.rationale), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.description), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.reason), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.summary), LOWER(CONCAT('%', @q, '%'))) "
                "LIMIT @limit RETURN {id: d._key, score: 0.0, title: d.title, type: d.type}"
            )
            cursor = self.db.aql.execute(aql, bind_vars={"q": q, "limit": limit})
            results = list(cursor)
        resp = {"query": q, "matches": results, "vector_used": False}
        self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
        return resp
