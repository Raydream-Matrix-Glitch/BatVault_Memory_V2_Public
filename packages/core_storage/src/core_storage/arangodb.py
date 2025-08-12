from __future__ import annotations

import hashlib
import socket
from urllib.parse import urlparse
import os
import re
import time
from typing import Any, Dict, List, Iterable, Sequence, Optional, Tuple

import httpx
from functools import cached_property
from pydantic import BaseModel

from core_config import get_settings
from core_logging import get_logger, log_stage
import core_metrics

logger = get_logger("core_storage")


class ArangoStore:
    """Storage adapter for Batvault memory graph on ArangoDB.

    This wrapper lazily connects to ArangoDB, creates missing collections,
    bootstraps indexes and search views, and exposes convenience helpers for
    upserts, catalog access, snapshot handling, graph expansion and text /
    vector search.
    """

    # ------------------------------------------------------------
    # Construction & connection
    # ------------------------------------------------------------

    def __init__(
        self,
        url: str | None = None,
        root_user: str | None = None,
        root_password: str | None = None,
        db_name: str | None = None,
        graph_name: str = "batvault_graph",
        catalog_col: str = "catalog",
        meta_col: str = "meta",
        *,
        client: object | None = None,
        lazy: bool = True,
    ) -> None:
        cfg = get_settings()
        self._url = url or cfg.arango_url
        self._root_user = root_user or cfg.arango_root_user
        self._root_password = root_password or cfg.arango_root_password
        self._db_name = db_name or cfg.arango_db
        self._graph_name = graph_name
        self._client = client  # injected stub for tests
        self.catalog_col, self.meta_col = catalog_col, meta_col
        self.db: Optional[object] = None
        self.graph: Optional[object] = None
        if not lazy:
            self._connect()

    def _connect(self) -> None:
        if self.db is not None:
            return
        if self._client is not None:
            self.db = self._client
            return
        # ── Fast-fail if ArangoDB is unreachable ────────────────────────────
        # Two probes:
        #   1. DNS lookup         → stub-mode if host *not* resolvable
        #   2. 50 ms TCP handshake→ stub-mode if port closed / no listener
        parsed = urlparse(self._url)
        host   = parsed.hostname or self._url
        port   = parsed.port or 8529

        try:
            socket.getaddrinfo(host, None)            # DNS probe
        except socket.gaierror:
            logger.warning("ArangoDB host '%s' not resolvable – stub-mode", host)
            self.db = self.graph = None
            return

        try:
            sock = socket.create_connection((host, port), timeout=0.05)
            sock.close()
        except OSError:
            logger.warning("ArangoDB %s:%s unreachable – stub-mode", host, port)
            self.db = self.graph = None
            return

        # DNS resolves *and* port accepts connections – continue with normal
        # driver initialisation.
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            logger.warning("ArangoDB host '%s' not resolvable – stub-mode", host)
            self.db = self.graph = None
            return
        try:
            from arango import ArangoClient

            t0 = time.perf_counter()
            client = ArangoClient(hosts=self._url)
            sys_db = client.db("_system", username=self._root_user, password=self._root_password)
            if not sys_db.has_database(self._db_name):
                sys_db.create_database(self._db_name)
            self.db = client.db(self._db_name, username=self._root_user, password=self._root_password)
            self.graph = (
                self.db.graph(self._graph_name)
                if self.db.has_graph(self._graph_name)
                else self.db.create_graph(self._graph_name)
            )
        except Exception as exc:
            logger.warning("ArangoDB unavailable – running in stub mode (%s)", exc)
            self.db = self.graph = None
        finally:
            core_metrics.histogram_ms(
                "arangodb.connection_latency_ms",
                (time.perf_counter() - t0) * 1_000,
                component="core_storage",
            )
        if self.db is None:
            return
        for name in ("nodes", "edges", self.catalog_col, self.meta_col):
            if not self.db.has_collection(name):
                self.db.create_collection(name, edge=(name == "edges"))
        if not self.graph or not self.graph.has_edge_definition("edges"):
            self.graph.create_edge_definition(
                edge_collection="edges",
                from_vertex_collections=["nodes"],
                to_vertex_collections=["nodes"],
            )
        self._ensure_search_components()
        if os.getenv("ARANGO_VECTOR_INDEX_ENABLED", "false").lower() == "true":
            self._audit_embedding_config()
            self._maybe_create_vector_index()

    # ------------------------------------------------------------
    # Search components (vector index, analyzer & view)
    # ------------------------------------------------------------

    def _ensure_search_components(self) -> None:
        cfg = get_settings()
        auth = httpx.BasicAuth(cfg.arango_root_user, cfg.arango_root_password)
        base = f"{cfg.arango_url}/_db/{self.db.name}"
        analyzer = {
            "name": "text_en",
            "type": "text",
            "properties": {
                "locale": "en_US.utf-8",
                "case": "lower",
                "accent": False,
                "stemming": True,
            },
        }
        try:
            httpx.post(f"{base}/_api/analyzer", json=analyzer, auth=auth, timeout=10.0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 409):
                raise
        try:
            httpx.post(
                f"{base}/_api/view",
                json={"name": "nodes_search", "type": "arangosearch"},
                auth=auth,
                timeout=10.0,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 409):
                raise
        view_props = {
            "links": {
                "nodes": {
                    "includeAllFields": False,
                    "fields": {
                        "rationale": {"analyzers": ["text_en"]},
                        "summary": {"analyzers": ["text_en"]},
                        "description": {"analyzers": ["text_en"]},
                        "reason": {"analyzers": ["text_en"]},
                        "option": {"analyzers": ["text_en"]},
                        "title": {"analyzers": ["text_en"]},
                    },
                    "storeValues": "id",
                }
            }
        }
        httpx.patch(
            f"{base}/_api/view/nodes_search/properties",
            json=view_props,
            auth=auth,
            timeout=10.0,
        )

    def _count_vectors(self) -> int:
        try:
            cursor = self.db.aql.execute(
                'RETURN LENGTH(FOR d IN nodes FILTER HAS(d, "embedding") RETURN 1)'
            )
            return int(next(cursor))
        except Exception as exc:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_estimate_warn", error=str(exc))
            return 0

    def _maybe_create_vector_index(self) -> None:
        cfg = get_settings()
        url = f"{cfg.arango_url}/_db/{self.db.name}/_api/index"
        params = {"collection": "nodes"}
        vectors = self._count_vectors()
        idx_type = os.getenv("ARANGO_VECTOR_INDEX_TYPE", "hnsw").lower()
        dim = int(os.getenv("EMBEDDING_DIM", 768))
        metric = os.getenv("VECTOR_METRIC", "cosine")

        def _hnsw_payload():
            return {
                "type": "vector",
                "name": "nodes_embedding_hnsw",
                "fields": ["embedding"],
                "inBackground": True,
                "params": {
                    "dimension": dim,
                    "metric": metric,
                    "indexType": "hnsw",
                    "M": int(os.getenv("HNSW_M", 16)),
                    "efConstruction": int(os.getenv("HNSW_EF", 200)),
                },
            }

        def _ivf_payload():
            return {
                "type": "vector",
                "name": "nodes_embedding_ivf",
                "fields": ["embedding"],
                "inBackground": True,
                "params": {
                    "dimension": dim,
                    "metric": metric,
                    "indexType": "ivf",
                    "nLists": int(os.getenv("IVF_NLISTS", 1024)),
                    "numProbes": int(os.getenv("IVF_NUMPROBES", 1)),
                },
            }

        payload = _ivf_payload() if idx_type == "ivf" else _hnsw_payload()
        auth = httpx.BasicAuth(cfg.arango_root_user, cfg.arango_root_password)
        try:
            resp = httpx.post(url, params=params, json=payload, auth=auth, timeout=10.0)
            common = {
                "status": resp.status_code,
                "index_name": payload["name"],
                "collection": "nodes",
                "dimension": payload["params"]["dimension"],
                "metric": payload["params"]["metric"],
                "M": payload["params"].get("M"),
                "efConstruction": payload["params"].get("efConstruction"),
                "vector_count": vectors,
            }
            if resp.status_code in (200, 201):
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_created", **common)
            elif resp.status_code == 409 or (
                resp.headers.get("content-type", "").startswith("application/json") and resp.json().get("errorNum") == 1210
            ):
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_exists", **common)
            else:
                # Compatibility retry: some Arango deployments expect IVF params (nLists)
                body_txt = resp.text[:500]
                wants_ivf = "nLists" in body_txt and payload["params"].get("indexType") == "hnsw"
                if wants_ivf:
                    ivf = _ivf_payload()
                    resp2 = httpx.post(url, params=params, json=ivf, auth=auth, timeout=10.0)
                    common["index_name"] = ivf["name"]
                    if resp2.status_code in (200, 201, 409):
                        log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_ivf_compat", **common)
                        return
                log_stage(
                    get_logger("memory_api"),
                    "bootstrap",
                    "arango_vector_index_warn",
                    body=body_txt,
                    **common,
                    payload_schema="vector+params/" + payload["params"].get("indexType", "unknown"),
                )
        except Exception as exc:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_error", error=str(exc))

    def _audit_embedding_config(self) -> None:
        cfg = get_settings()
        dim = int(getattr(cfg, "embedding_dim", 0))
        metric = str(getattr(cfg, "vector_metric", "cosine")).lower()
        ok_dim = dim > 0
        ok_metric = metric in {"cosine", "l2"}
        fp = hashlib.sha1(f"{dim}|{metric}".encode()).hexdigest()[:12]
        log_stage(
            get_logger("memory_api"),
            "bootstrap",
            "embedding_config",
            embedding_dim=dim,
            embedding_metric=metric,
            config_fingerprint=fp,
            valid_dim=ok_dim,
            valid_metric=ok_metric,
        )
        if not ok_dim or not ok_metric:
            raise ValueError(f"Invalid embedding configuration: dim={dim}, metric='{metric}'")

    # ------------------------------------------------------------
    # Upsert helpers
    # ------------------------------------------------------------

    def upsert_node(self, node_id: str, node_type: str, payload: Dict[str, Any]) -> None:
        """Insert or replace a node document after applying normalisation.

        Prior to writing to the ``nodes`` collection this method applies
        the shared normalisation routine to the provided payload.  This
        guarantees that all persisted documents adhere to the canonical
        schema (``x-extra`` presence, tag formatting, allowed field
        filtering and timestamp coercion).  The normalised payload is
        then augmented with the Arango-specific ``_key`` and ``type``
        keys and written using an upsert (insert with overwrite).
        """
        self._connect()
        # Import within the method to avoid creating dependency cycles.
        # If the shared normaliser cannot be imported (e.g. during tests),
        # fall back to verbatim storage.  Import errors are swallowed to
        # allow ingestion to proceed.
        try:
            from shared.normalize import (
                normalize_decision,
                normalize_event,
                normalize_transition,
            )
        except Exception:
            normalised = dict(payload)
        else:
            if node_type == "decision":
                normalised = normalize_decision(payload)
            elif node_type == "event":
                normalised = normalize_event(payload)
            elif node_type == "transition":
                normalised = normalize_transition(payload)
            else:
                normalised = dict(payload)
        # Attach the storage specific identifiers.  The node id maps to
        # the Arango ``_key``; type is preserved if present on the
        # normalised document but overwritten to ensure consistency.
        doc = dict(normalised)
        doc["_key"] = node_id
        doc["type"] = node_type
        # In stub-mode ``self.db`` may be None; guard against AttributeError
        if self.db is not None:
            self.db.collection("nodes").insert(doc, overwrite=True)

    def upsert_edge(
        self,
        edge_id: str,
        from_id: str,
        to_id: str,
        rel_type: str,
        payload: Dict[str, Any],
    ) -> None:
        safe_key = self._safe_key(edge_id)
        if safe_key != edge_id:
            logger.info("edge_key_sanitised", extra={"raw": edge_id, "sanitised": safe_key, "stage": "storage"})
        doc = dict(payload)
        doc.update(
            {
                "_key": safe_key,
                "_from": f"nodes/{from_id}",
                "_to": f"nodes/{to_id}",
                "type": rel_type,
            }
        )
        self.db.collection("edges").insert(doc, overwrite=True)

    # ------------------------------------------------------------
    # Key & cache helpers
    # ------------------------------------------------------------

    _ILLEGAL_CHARS = re.compile(r"[^A-Za-z0-9_\-:\.]")

    def _safe_key(self, raw: str) -> str:
        cleaned = self._ILLEGAL_CHARS.sub("_", raw)
        if len(cleaned.encode()) <= 254:
            return cleaned
        digest = hashlib.sha1(cleaned.encode()).hexdigest()[:8]
        return f"{cleaned[:245]}_{digest}"

    def _cache_key(self, *parts: str) -> str:
        etag = self.get_snapshot_etag() or "noetag"
        return ":".join((etag, *parts))

    # ------------------------------------------------------------
    # Catalog API
    # ------------------------------------------------------------

    def set_field_catalog(self, catalog: Dict[str, List[str]]) -> None:
        self.db.collection(self.catalog_col).insert({"_key": "fields", "fields": catalog}, overwrite=True)

    def set_relation_catalog(self, relations: List[str]) -> None:
        self.db.collection(self.catalog_col).insert({"_key": "relations", "relations": relations}, overwrite=True)

    def get_field_catalog(self) -> Dict[str, List[str]]:
        doc = self.db.collection(self.catalog_col).get("fields") or {"fields": {}}
        return doc["fields"]

    def get_relation_catalog(self) -> List[str]:
        doc = self.db.collection(self.catalog_col).get("relations") or {"relations": []}
        return doc["relations"]

    # ------------------------------------------------------------
    # Snapshot handling
    # ------------------------------------------------------------

    def set_snapshot_etag(self, etag: str) -> None:
        self.db.collection(self.meta_col).insert({"_key": "snapshot", "etag": etag}, overwrite=True)

    def get_snapshot_etag(self) -> Optional[str]:
        if self.db is None:
            self._connect()
        if self.db is None or not hasattr(self.db, "collection"):
            return ""
        doc = self.db.collection(self.meta_col).get("snapshot")
        return doc.get("etag") if doc else None

    def prune_stale(self, snapshot_etag: str) -> Tuple[int, int]:
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

    # ------------------------------------------------------------
    # Enrichment helpers
    # ------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the raw node document from the ``nodes`` collection or
        ``None`` when no database connection is available or the node
        cannot be found.

        In "stub‑mode" (e.g. during tests or when ArangoDB is unreachable),
        ``self.db`` is ``None`` to signal the absence of a backing store.
        Accessing methods on ``None`` would raise an ``AttributeError`` which
        then bubbles up into FastAPI handlers.  This helper guards against
        that by returning ``None`` if the underlying database connection has
        not been established.  When connected, any errors raised by the
        underlying driver (such as missing documents) are caught and treated
        as a missing node.
        """
        # Ensure we have a database connection; in stub‑mode this will
        # short‑circuit and return ``None``.
        if self.db is None or not hasattr(self.db, "collection"):
            return None
        try:
            return self.db.collection("nodes").get(node_id)
        except Exception:
            # On any lookup error (e.g. missing document), behave as
            # though the node does not exist.  This prevents upstream
            # callers from crashing when a node is not found.
            return None

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

    # ------------------------------------------------------------
    # Redis-backed caching utilities
    # ------------------------------------------------------------

    def _redis(self):
        try:
            import redis  # type: ignore

            return redis.Redis.from_url(get_settings().redis_url)
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

                core_metrics.counter("cache_hit_total", 1, service="memory_api")
                return orjson.loads(v)
            core_metrics.counter("cache_miss_total", 1, service="memory_api")
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

    # ------------------------------------------------------------
    # Graph expansion (k = 1)
    # ------------------------------------------------------------

    def expand_candidates(self, anchor_id: str, k: int = 1) -> dict:
        if self.db is None:
            self._connect()
        # Stub-mode fallback (Arango unavailable).  Keep the new canonical key
        # `node_id`; keep `anchor` as a temporary alias for backward-compat.
        if self.db is None:
            return {
                "node_id": anchor_id,
                "anchor":  anchor_id,
                "neighbors": [],
                "meta": {"snapshot_etag": ""},
            }
        k = 1
        cache_key = self._cache_key("expand", anchor_id, f"k{k}")
        cached = self._cache_get(cache_key)
        if cached:
            if isinstance(cached.get("neighbors"), dict):
                cached = {
                    **cached,
                    "neighbors": (cached["neighbors"].get("events") or []) + (cached["neighbors"].get("transitions") or []),
                }
            # Milestone-4 contract normalisation
            if "anchor" not in cached and cached.get("node_id") is not None:
                cached = {**cached, "anchor": cached["node_id"]}
            if "node_id" not in cached and cached.get("anchor") is not None:
                cached = {**cached, "node_id": cached["anchor"]}
            return cached
        aql = """
        LET anchor = DOCUMENT('nodes', @anchor)
        LET outgoing = (FOR v,e IN 1..1 OUTBOUND anchor GRAPH @graph RETURN {node: v, edge: e})
        LET incoming = (FOR v,e IN 1..1 INBOUND  anchor GRAPH @graph RETURN {node: v, edge: e})
        LET sup = (
            FOR sid IN anchor.supported_by
              LET ev = DOCUMENT('nodes', sid)
              FILTER ev != NULL RETURN { node: ev, edge: { relation: 'LED_TO' } }
        )
        RETURN { anchor: anchor, neighbors: UNIQUE(APPEND(APPEND(outgoing, incoming), sup)) }
        """
        cursor = self.db.aql.execute(aql, bind_vars={"anchor": anchor_id, "graph": self._graph_name})
        docs   = self._cursor_to_list(cursor)
        doc    = docs[0] if docs else {"anchor": None, "neighbors": []}
        # Canonical key is `node_id`; `anchor` retained as alias until v3.
        anchor_doc = doc.get("anchor") or doc.get("node_id")
        result = {
            "node_id": anchor_doc,
            "anchor":  anchor_doc,
            "neighbors": [
                {
                    "id": n["node"].get("_key"),
                    "type": n["node"].get("type"),
                    "title": n["node"].get("title"),
                    "edge": {
                        # Map the stored ``relation`` into ``rel`` for
                        # downstream consumers.  When the relation key is
                        # missing or None we propagate None explicitly.
                        "rel": (n.get("edge") or {}).get("relation"),
                        "timestamp": (n.get("edge") or {}).get("timestamp"),
                    },
                }
                for n in doc.get("neighbors", [])
                if n.get("node") and n.get("edge")
            ],
        }
        self._cache_set(cache_key, result, get_settings().cache_ttl_expand_sec)
        return result

    # ------------------------------------------------------------
    # Text & vector resolver
    # ------------------------------------------------------------

    def resolve_text(
        self,
        q: str,
        limit: int = 10,
        use_vector: bool = False,
        query_vector: List[float] | None = None,
    ) -> dict:
        if self.db is None:
            try:
                self._connect()
            except Exception:
                return {"query": q, "matches": [], "vector_used": False}
        if self.db is None:                       # still not available
            return {"query": q, "matches": [], "vector_used": False}
        settings = get_settings()
        if settings.enable_embeddings and not use_vector:
            _embed = globals().get("embed")
            if callable(_embed):
                try:
                    query_vector = _embed(q)  # type: ignore[arg-type]
                    use_vector = True
                except Exception:
                    use_vector = False
        key = self._cache_key("resolve", str(hash((q, bool(use_vector)))), f"l{limit}")
        cached = self._cache_get(key)
        if cached:
            cached.setdefault("query", q)
            cached.setdefault("matches", [])
            cached.setdefault("vector_used", False)
            cached.setdefault("resolved_id", q)
            cached.setdefault("meta", {})
            return cached
        if self.db is None:
            self._connect()
        if self.db is None:
            return {
                "query": q,
                "matches": [],
                "vector_used": bool(use_vector),
                "resolved_id": q,
                "meta": {"snapshot_etag": ""},
            }
        results: List[Dict[str, Any]] = []
        if use_vector and settings.enable_embeddings:
            vector_idx_enabled = os.getenv("ARANGO_VECTOR_INDEX_ENABLED", "false").lower() == "true"
            if vector_idx_enabled and query_vector is not None:
                try:
                    aql = (
                        "FOR d IN nodes FILTER HAS(d,'embedding') "
                        "LET score = COSINE_SIMILARITY(d.embedding, @qv) "
                        "SORT score DESC LIMIT @limit "
                        "RETURN {id: d._key, score: score, title: d.title, type: d.type}"
                    )
                    # Bind the embedding under ``@qv``.  Passing the raw query under
                    # ``q`` previously left @qv undefined and prevented cosine similarity
                    # from working when the vector index is disabled.
                    cursor  = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                    results = self._cursor_to_list(cursor)
                    if hasattr(self.db, "aql"):
                        self.db.aql.latest_query = aql  # type: ignore[attr-defined]
                    resp = {"query": q, "matches": results, "vector_used": True}
                    self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
                    return resp
                except Exception:
                    pass
            elif use_vector and not vector_idx_enabled and query_vector is not None:
                try:
                    aql = (
                        "FOR d IN nodes FILTER HAS(d,'embedding') "
                        "LET score = COSINE_SIMILARITY(d.embedding, @qv) "
                        "SORT score DESC LIMIT @limit "
                        "RETURN {id: d._key, score: score, title: d.title, type: d.type}"
                    )
                    cursor  = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                    results = self._cursor_to_list(cursor)
                    if hasattr(self.db, "aql"):
                        self.db.aql.latest_query = aql  # type: ignore[attr-defined]
                    resp = {"query": q, "matches": results, "vector_used": True}
                    self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
                    return resp
                except Exception:
                    pass
        try:
            # The BM25 search uses the ArangoSearch view ``nodes_search``.  In addition
            # to rationale, description, reason and summary, include the decision
            # ``option`` and ``title`` fields so that queries about the action itself
            # (e.g. “Exit plasma TV production”) can match the associated decision.
            aql = (
                "FOR d IN nodes_search "
                "SEARCH ANALYZER( "
                "  TOKENS(@q,'text_en') ANY IN d.option OR "
                "  TOKENS(@q,'text_en') ANY IN d.title OR "
                "  TOKENS(@q,'text_en') ANY IN d.rationale OR "
                "  TOKENS(@q,'text_en') ANY IN d.summary OR "
                "  TOKENS(@q,'text_en') ANY IN d.description OR "
                "  TOKENS(@q,'text_en') ANY IN d.reason, 'text_en' ) "
                "SORT BM25(d) DESC LIMIT @limit "
                "RETURN {id: d._key, score: BM25(d), title: d.title, type: d.type}"
            )
            cursor = self.db.aql.execute(aql, bind_vars={"q": q, "limit": limit})
            results = list(cursor)
            if not results:
                try:
                    import re as _re
                    terms = [t for t in _re.findall(r"\w+", q.lower()) if len(t) >= 3]
                except Exception:
                    terms = []
                if terms:
                    fields = ["option","title","rationale","summary","description","reason"]
                    ors = " OR ".join([f"LIKE(LOWER(d.{f}), LOWER(CONCAT('%', @t, '%')))" for f in fields])
                    aql_like = ("FOR t IN @terms FOR d IN nodes FILTER " + ors +
                                " COLLECT d = d WITH COUNT INTO _c LIMIT @limit "
                                " RETURN {id: d._key, score: 0.0, title: d.title, type: d.type}")
                    cursor = self.db.aql.execute(aql_like, bind_vars={"terms": terms, "limit": limit})
                    results = list(cursor)
                    try:
                        from core_logging import log_stage, get_logger
                        log_stage(get_logger("memory_api"), "resolver", "bm25_zero_hits_like_fallback", q=q, terms=len(terms))
                    except Exception:
                        pass
        except Exception:
            # Fallback lexical search when ArangoSearch view fails (view missing or unsupported).
            # Extend the LIKE filters to include options and titles so queries referencing
            # the decision’s action rather than its rationale are still captured.
            aql = (
                "FOR d IN nodes "
                "FILTER LIKE(LOWER(d.rationale), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.description), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.reason), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.summary), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.option), LOWER(CONCAT('%', @q, '%'))) "
                "  OR LIKE(LOWER(d.title), LOWER(CONCAT('%', @q, '%'))) "
                "LIMIT @limit RETURN {id: d._key, score: 0.0, title: d.title, type: d.type}"
            )
            cursor = self.db.aql.execute(aql, bind_vars={"q": q, "limit": limit})
            results = list(cursor)
        resp = {"query": q, "matches": results, "vector_used": False}
        self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
        return resp
    
    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _cursor_to_list(cursor: Any) -> List[Dict[str, Any]]:
        try:
            return list(cursor)
        except TypeError:
            pass

        for attr in ("results", "_result"):
            if hasattr(cursor, attr):
                obj = getattr(cursor, attr)
                if isinstance(obj, (list, tuple)):
                    return list(obj)
                
        if isinstance(cursor, (list, tuple)):
            return list(cursor)
        return []
