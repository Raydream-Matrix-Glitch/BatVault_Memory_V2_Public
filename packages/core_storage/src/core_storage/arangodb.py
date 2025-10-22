import hashlib
import socket
from urllib.parse import urlparse
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx  # retained for types; calls go through core_http
from core_http import fetch_json_sync
from core_models.ontology import NodeType, EdgeType
# Import OTEL context injector so all outgoing HTTP calls carry the current trace.
try:
    from core_observability import inject_trace_context  # type: ignore
except Exception:
    inject_trace_context = None  # type: ignore
from core_config import get_settings
from core_logging import get_logger, log_stage, trace_span, current_request_id
from core_config.constants import timeout_for_stage
import core_metrics
from core_utils import jsonx
from core_utils.domain import anchor_to_storage_key


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
        self._core_indexes_ok = False
        if not lazy:
            self._connect()

    def _bootstrap_verbose(self) -> bool:
        return os.getenv("ARANGO_BOOTSTRAP_VERBOSE", "0") == "1"

    # Environment helpers
    def _is_dev(self) -> bool:
        """
        Return True when running in a development environment.
        Uses core_config settings if present, falls back to ENVIRONMENT.
        """
        try:
            env = getattr(get_settings(), "environment", None) or os.getenv("ENVIRONMENT", "dev")
            return str(env).lower() == "dev"
        except Exception:
            return os.getenv("ENVIRONMENT", "dev").lower() == "dev"

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
            if self._is_dev():
                logger.warning("ArangoDB host '%s' not resolvable – stub-mode (DEV)", host)
                self.db = self.graph = None
                return
            logger.error("ArangoDB host '%s' not resolvable – abort (non-DEV)", host)
            raise RuntimeError(f"ArangoDB host '{host}' not resolvable (non-DEV)")

        try:
            sock = socket.create_connection((host, port), timeout=0.05)
            sock.close()
        except OSError:
            if self._is_dev():
                logger.warning("ArangoDB %s:%s unreachable – stub-mode (DEV)", host, port)
                self.db = self.graph = None
                return
            logger.error("ArangoDB %s:%s unreachable – abort (non-DEV)", host, port)
            raise RuntimeError(f"ArangoDB {host}:{port} unreachable (non-DEV)")

        # DNS resolves *and* port accepts connections – continue with normal
        # driver initialisation.
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            if self._is_dev():
                logger.warning("ArangoDB host '%s' not resolvable – stub-mode (DEV)", host)
                self.db = self.graph = None
                return
            logger.error("ArangoDB host '%s' not resolvable – abort (non-DEV)", host)
            raise RuntimeError(f"ArangoDB host '{host}' not resolvable (non-DEV)")
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
            if self._is_dev():
                logger.warning("ArangoDB unavailable – stub mode (DEV) (%s)", exc)
                self.db = self.graph = None
            else:
                logger.error("ArangoDB unavailable – abort (non-DEV): %s", exc)
                raise
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
            # Bootstrap verbosity is opt-in; avoid noisy logs by default.
            if self._bootstrap_verbose():
                self._audit_embedding_config()
                self._maybe_create_vector_index()
            else:
                # Still validate config (raise if invalid), but do it silently.
                self._audit_embedding_config(silent=True); self._maybe_create_vector_index(silent=True)


    def ready(self) -> bool:
        """Return True when a real database connection is established."""
        return self.db is not None

    # ------------------------------------------------------------
    # Search components (vector index, analyzer & view)
    # ------------------------------------------------------------

    def _ensure_search_components(self) -> None:
        verbose = self._bootstrap_verbose()
        if verbose:
            with trace_span("storage.arango.ensure_search", stage="storage") as sp:
                try:
                    sp.set_attribute("analyzer", "text_en")
                    sp.set_attribute("view", "nodes_search")
                    sp.set_attribute("collection", "nodes")
                except Exception:
                    pass

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

        headers = inject_trace_context({}) if inject_trace_context else {}
        # Include the current request id for audit correlation
        try:
            _rid = current_request_id()
            if _rid:
                headers.setdefault("x-request-id", _rid)
        except Exception:
            pass
        # Create analyzer (idempotent). Silent unless unexpected error.
        if verbose:
            with trace_span("storage.arango.http.create_analyzer", stage="storage"):
                resp = httpx.post(
                    f"{base}/_api/analyzer", json=analyzer, auth=auth,
                    timeout=timeout_for_stage("enrich"), headers=headers
                )
        else:
            resp = httpx.post(
                f"{base}/_api/analyzer", json=analyzer, auth=auth,
                timeout=timeout_for_stage("enrich"), headers=headers
            )
            if resp.status_code not in (200, 201, 400, 409):
                log_stage(get_logger("storage"), "bootstrap", "arango_bootstrap_error",
                          step="create_analyzer", status=int(resp.status_code), body=resp.text[:240],
                          request_id=(current_request_id() or "startup"))
        # Create ArangoSearch view (idempotent).
        payload_view = {"name": "nodes_search", "type": "arangosearch"}
        if verbose:
            with trace_span("storage.arango.http.create_view", stage="storage"):
                resp2 = httpx.post(
                    f"{base}/_api/view", json=payload_view, auth=auth,
                    timeout=timeout_for_stage("enrich"), headers=headers
                )
        else:
            resp2 = httpx.post(
                f"{base}/_api/view", json=payload_view, auth=auth,
                timeout=timeout_for_stage("enrich"), headers=headers
            )
            if resp2.status_code not in (200, 201, 400, 409):
                log_stage(get_logger("storage"), "bootstrap", "arango_bootstrap_error",
                          step="create_view", status=int(resp2.status_code), body=resp2.text[:240],
                          request_id=(current_request_id() or "startup"))
        # Define view properties incl. indexed fields; PATCH creates/updates.
        # Index all present and future node fields; strings analyzed by text_en.
        view_props = {
            "links": {
                "nodes": {
                    "includeAllFields": True,
                    "analyzers": ["text_en"],
                    "storeValues": "id",
                }
            }
        }
        if verbose:
            with trace_span("storage.arango.http.patch_view", stage="storage"):
                resp3 = httpx.patch(
                    f"{base}/_api/view/nodes_search/properties",
                    json=view_props, auth=auth,
                    timeout=timeout_for_stage("enrich"), headers=headers
                )
        else:
            resp3 = httpx.patch(
                f"{base}/_api/view/nodes_search/properties",
                json=view_props, auth=auth,
                timeout=timeout_for_stage("enrich"), headers=headers
            )
            if resp3.status_code not in (200, 201):
                # 400/409 here usually means "already configured" which PATCH shouldn't return;
                # still keep bootstrap quiet unless it's clearly unexpected.
                if resp3.status_code not in (400, 409):
                    log_stage(get_logger("storage"), "bootstrap", "arango_bootstrap_error",
                              step="patch_view", status=int(resp3.status_code), body=resp3.text[:240],
                              request_id=(current_request_id() or "startup"))

    def _count_vectors(self) -> int:
        try:
            cursor = self.db.aql.execute(
                'RETURN LENGTH(FOR d IN nodes FILTER HAS(d, "embedding") RETURN 1)'
            )
            return int(next(cursor))
        except (RuntimeError, ValueError, TypeError, StopIteration) as exc:
            log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_estimate_warn",
                      error=str(exc), request_id=(current_request_id() or "startup"))
            return 0

    def _maybe_create_vector_index(self, silent: bool = False) -> None:
        """Create a vector index on nodes.embedding (HNSW or IVF).

        Chooses the primary index type via ARANGO_VECTOR_INDEX_TYPE ("hnsw" or "ivf")
        and gracefully falls back to the other if the first attempt fails or is incompatible.
        """
        cfg = get_settings()
        url = f"{cfg.arango_url}/_db/{self.db.name}/_api/index"
        params = {"collection": "nodes"}
        vectors = self._count_vectors()

        idx_type = os.getenv("ARANGO_VECTOR_INDEX_TYPE", "hnsw").lower()
        dim = int(os.getenv("EMBEDDING_DIM", 768))
        metric = os.getenv("VECTOR_METRIC", "cosine")
        # Plan attributes for tracing
        try:
            with trace_span("storage.arango.index.plan", stage="storage") as sp:
                sp.set_attribute("index_type", idx_type)
                sp.set_attribute("embedding_dim", dim)
                sp.set_attribute("vector_metric", metric)
        except Exception:
            pass

        def _hnsw_payload() -> dict:
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

        def _ivf_payload() -> dict:
            # Build IVF index parameters.  Include numProbes only when the
            # IVF_NUMPROBES environment variable is explicitly set, because
            # older ArangoDB versions return HTTP 400 for unknown attributes.
            params = {
                "dimension": dim,
                "metric": metric,
                "indexType": "ivf",
                "nLists": int(os.getenv("IVF_NLISTS", 1024)),
            }
            num_probes_env = os.getenv("IVF_NUMPROBES")
            if num_probes_env is not None:
                try:
                    params["numProbes"] = int(num_probes_env)
                except (OSError, httpx.HTTPError, ValueError, RuntimeError):
                    # Ignore invalid values; omitting numProbes is always safe
                    pass
            return {
                "type": "vector",
                "name": "nodes_embedding_ivf",
                "fields": ["embedding"],
                "inBackground": True,
                "params": params,
            }

        # Prepare both payloads; env var selects which to try first.
        primary_payload = _ivf_payload() if idx_type == "ivf" else _hnsw_payload()
        fallback_payload = _hnsw_payload() if idx_type == "ivf" else _ivf_payload()

        auth = httpx.BasicAuth(cfg.arango_root_user, cfg.arango_root_password)

        def _create(payload: dict) -> httpx.Response:
            """
            Create a vector index on the ``nodes`` collection.  Wrap the call in a
            span and attach key attributes.  Propagate the current trace context
            via headers on the outbound HTTP request.
            """
            with trace_span("storage.arango.http.index_create", stage="storage") as sp:
                try:
                    sp.set_attribute("index_name", payload.get("name"))
                    sp.set_attribute("index_type", payload.get("params", {}).get("indexType"))
                    sp.set_attribute("dimension", payload.get("params", {}).get("dimension"))
                    sp.set_attribute("metric", payload.get("params", {}).get("metric"))
                    sp.set_attribute("collection", "nodes")
                    sp.set_attribute("timeout_ms", 10_000)
                except Exception:
                    pass
                hdrs = inject_trace_context({}) if inject_trace_context else {}
                try:
                    _rid = current_request_id()
                    if _rid:
                        hdrs.setdefault("x-request-id", _rid)
                except Exception:
                    pass
                _t0 = time.perf_counter()
                resp = httpx.post(
                    url,
                    params=params,
                    json=payload,
                    auth=auth,
                    timeout=timeout_for_stage("enrich"),
                    headers=hdrs,
                )
                try:
                    sp.set_attribute("duration_ms", int((time.perf_counter() - _t0) * 1000))
                    sp.set_attribute("status_code", getattr(resp, "status_code", 0))
                except Exception:
                    pass
                return resp

        for attempt, payload in enumerate((primary_payload, fallback_payload)):
            try:
                resp = _create(payload)
            except Exception as exc:
                if not silent:
                    log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_error", error=str(exc))
                break

            common = {
                "status": resp.status_code,
                "index_name": payload["name"],
                "collection": "nodes",
                "dimension": payload["params"]["dimension"],
                "metric": payload["params"]["metric"],
                "M": payload["params"].get("M"),
                "efConstruction": payload["params"].get("efConstruction"),
                "nLists": payload["params"].get("nLists"),
                "numProbes": payload["params"].get("numProbes"),
                "vector_count": vectors,
            }
            if resp.status_code in (200, 201):
                if not silent:
                    log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_created", **common)
                return
            # Already exists (duplicate index): HTTP 409 or errorNum 1210 in JSON body.
            if resp.status_code == 409 or (
                resp.headers.get("content-type", "").startswith("application/json")
                and resp.json().get("errorNum") == 1210
            ):
                log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_exists", **common)
                return

            # Some ArangoDB versions don't accept 'numProbes' or other IVF
            # tuning parameters – retry IVF without unknown attributes.  We
            # perform a case‑insensitive search on the error message to
            # determine whether to retry.  On retry we strip the offending
            # keys ('numProbes', 'nLists') and recompute the logging payload.
            body_txt = resp.text[:500]
            if attempt == 0 and "nLists" in body_txt:
                continue

            err = body_txt.lower()
            if (
                payload["params"].get("indexType") == "ivf"
                and ("numprobes" in err or "nlists" in err or "unexpected attribute" in err)
            ):
                try:
                    _p2 = dict(payload)
                    _p2["params"] = {k: v for k, v in payload["params"].items() if k not in {"numProbes", "nLists"}}
                    hdrs = inject_trace_context({}) if inject_trace_context else {}
                    resp2 = httpx.post(
                        url,
                        params=params,
                        json=_p2,
                        auth=auth,
                        timeout=30.0,
                        headers=hdrs,
                    )
                    if resp2.status_code in (200, 201):
                        # Update common logging metadata to reflect the actual index parameters
                        common_retry = dict(common)
                        common_retry["nLists"] = _p2["params"].get("nLists")
                        common_retry["numProbes"] = _p2["params"].get("numProbes")
                        log_stage(get_logger("memory_api"), "bootstrap", "arango_vector_index_created",
                                  request_id=(current_request_id() or "startup"), **common_retry)
                        return
                except Exception:
                    pass

            if not silent:
                log_stage(
                    get_logger("memory_api"),
                    "bootstrap",
                    "arango_vector_index_warn",
                    body=body_txt,
                    **common,
                    payload_schema="vector+params/" + payload["params"].get("indexType", "unknown"),
                )
            return

    def _audit_embedding_config(self, silent: bool = False) -> None:
        cfg = get_settings()
        dim = int(getattr(cfg, "embedding_dim", 0))
        metric = str(getattr(cfg, "vector_metric", "cosine")).lower()
        ok_dim = dim > 0
        ok_metric = metric in {"cosine", "l2"}
        fp = hashlib.sha1(f"{dim}|{metric}".encode()).hexdigest()[:12]
        if not silent:
            log_stage(
            get_logger("memory_api"),
            "bootstrap",
            "embedding_config",
            embedding_dim=dim,
            embedding_metric=metric,
            config_fingerprint=fp,
            valid_dim=ok_dim,
            valid_metric=ok_metric,
            request_id=(current_request_id() or "startup"),
            )
        if not ok_dim or not ok_metric:
            raise ValueError(f"Invalid embedding configuration: dim={dim}, metric='{metric}'")


    # ------------------------------------------------------------------
    # Bulk-first write API (public) with micro-batching and retries
    # ------------------------------------------------------------------
    def upsert_nodes(self, docs: List[Dict[str, Any]], snapshot_etag: Optional[str] = None) -> Dict[str, Any]:
        """Bulk upsert nodes.
        Returns a summary dict: {batches,written,deduped,rejected,errors:[...]}.
        This is the **only** public node write method.
        """
        self._connect()
        self._ensure_core_indexes_once()
        summary = {"batches": 0, "written": 0, "deduped": 0, "rejected": 0, "errors": []}
        if self.db is None:
            if self._is_dev():
                return summary
            raise RuntimeError("Storage unavailable (non-DEV): cannot upsert nodes")

        mb_size = int(os.getenv("STORAGE_MICROBATCH_SIZE", "1000"))
        max_retries = int(os.getenv("STORAGE_MAX_RETRIES", "3"))
        base_ms = int(os.getenv("HTTP_RETRY_BASE_MS", "50"))
        jitter_ms = int(os.getenv("HTTP_RETRY_JITTER_MS", "200"))

        def _sanitize(d: Dict[str, Any]) -> Dict[str, Any]:
            """
            Accept all schema-validated fields (validator is authoritative).
            Only strip obvious legacy junk keys; compute Arango internals.
            """
            LEGACY_BLOCKLIST = {
                "preceding","succeeding","supported_by","based_on","led_to",
                "preceding_ids","adjacency","summary","snippet","rationale","option","reason","tags"
            }
            doc = {k: v for k, v in (d or {}).items() if k not in LEGACY_BLOCKLIST}
            # On-wire ID is the ANCHOR "<domain>#<id>"; storage _key maps '#'→'_'
            dom, nid = doc.get("domain"), doc.get("id")
            if not dom or not nid:
                raise ValueError(f"node missing domain/id for anchor key: {doc}")
            anchor = f"{dom}#{nid}"
            doc["_key"] = self._safe_key(anchor_to_storage_key(anchor))
            if snapshot_etag:
                doc["snapshot_etag"] = snapshot_etag
            return doc

        batch_no = 0
        for i in range(0, len(docs), mb_size):
            batch = [ _sanitize(x) for x in docs[i:i+mb_size] ]
            batch_no += 1
            summary["batches"] += 1
            attempt = 0
            while True:
                try:
                    created, updated = self._bulk_upsert_nodes_fast(batch)
                    summary["written"] += int(created + updated)
                    summary["deduped"] += int(updated)
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        log_stage(get_logger("storage"), "storage", "upsert_nodes_batch_failed",
                                  batch=batch_no, error=str(exc))
                        # Fallback to per-doc insert to isolate errors
                        n_ok = 0
                        for d in batch:
                            try:
                                self.db.collection("nodes").insert(d, overwrite=True)
                                n_ok += 1
                            except Exception as doc_exc:
                                summary["rejected"] += 1
                                summary["errors"].append({"doc_id": d.get("id"), "reason": str(doc_exc)})
                        summary["written"] += n_ok
                        break
                    backoff = (base_ms * (2 ** (attempt - 1)) + int(os.urandom(1)[0] % max(1, jitter_ms))) / 1000.0
                    time.sleep(backoff)

        return summary

    def upsert_edges(self, docs: List[Dict[str, Any]], snapshot_etag: Optional[str] = None) -> Dict[str, Any]:
        """Bulk upsert edges.
        Returns a summary dict: {batches,written,deduped,rejected,errors:[...]}.
        This is the **only** public edge write method.
        """
        self._connect()
        self._ensure_core_indexes_once()
        summary = {"batches": 0, "written": 0, "deduped": 0, "rejected": 0, "errors": []}
        if self.db is None:
            if self._is_dev():
                return summary
            raise RuntimeError("Storage unavailable (non-DEV): cannot upsert edges")

        mb_size = int(os.getenv("STORAGE_MICROBATCH_SIZE", "1000"))
        max_retries = int(os.getenv("STORAGE_MAX_RETRIES", "3"))
        base_ms = int(os.getenv("HTTP_RETRY_BASE_MS", "50"))
        jitter_ms = int(os.getenv("HTTP_RETRY_JITTER_MS", "200"))

        def _sanitize_edge(e: Dict[str, Any]) -> Dict[str, Any]:
            """
            Minimal transformation only:
            - preserve all incoming fields (validator/ingest are authoritative)
            - add Arango internals: _key, _from, _to
            - add optional snapshot_etag
            """
            d = dict(e or {})
            _id, _f, _t = d.get("id"), d.get("from"), d.get("to")
            if not _id or not _f or not _t or "#" not in _f or "#" not in _t:
                raise ValueError(f"invalid edge (id/from/to) or anchor format: {d!r}")
            d["_key"]  = self._safe_key(str(_id))
            d["_from"] = f"nodes/{self._safe_key(anchor_to_storage_key(_f))}"
            d["_to"]   = f"nodes/{self._safe_key(anchor_to_storage_key(_t))}"
            # Do not persist legacy from/to copies; Arango stores only _from/_to.
            for k in ("from","to"):
                d.pop(k, None)
            if snapshot_etag:
                d["snapshot_etag"] = snapshot_etag
            return d

        batch_no = 0
        for i in range(0, len(docs), mb_size):
            batch = [ _sanitize_edge(x) for x in docs[i:i+mb_size] ]
            batch_no += 1
            summary["batches"] += 1
            attempt = 0
            while True:
                try:
                    created, updated = self._bulk_upsert_edges_fast(batch)
                    summary["written"] += int(created + updated)
                    summary["deduped"] += int(updated)
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        log_stage(get_logger("storage"), "storage", "upsert_edges_batch_failed",
                                  batch=batch_no, error=str(exc))
                        n_ok = 0
                        for d in batch:
                            try:
                                self.db.collection("edges").insert(d, overwrite=True)
                                n_ok += 1
                            except Exception as doc_exc:
                                summary["rejected"] += 1
                                summary["errors"].append({"doc_id": d.get("id"), "reason": str(doc_exc)})
                        summary["written"] += n_ok
                        break
                    backoff = (base_ms * (2 ** (attempt - 1)) + int(os.urandom(1)[0] % max(1, jitter_ms))) / 1000.0
                    time.sleep(backoff)

        return summary

    def _ensure_core_indexes_once(self) -> None:
        if getattr(self, "_core_indexes_ok", False):
            return
        try:
            self.ensure_core_indexes()
            self._core_indexes_ok = True
        except Exception:
            pass

    def ensure_core_indexes(self) -> None:
        """Ensure unique indexes for nodes(domain,id) and edges(id)."""
        if self.db is None:
            return
        cfg = get_settings()
        auth = httpx.BasicAuth(cfg.arango_root_user, cfg.arango_root_password)
        base = f"{cfg.arango_url}/_db/{self.db.name}"
        headers = {}
        if inject_trace_context:
            headers.update(inject_trace_context({}))
        # nodes(domain,id) unique
        payload_nodes = {"type": "persistent", "name": "uniq_nodes_domain_id",
                         "fields": ["domain","id"], "unique": True}
        try:
            httpx.post(f"{base}/_api/index", params={"collection":"nodes"},
                       json=payload_nodes, auth=auth, timeout=5.0, headers=headers)
        except Exception:
            pass
        # edges(id) unique
        payload_edges = {"type": "persistent", "name": "uniq_edges_id",
                         "fields": ["id"], "unique": True}
        try:
            httpx.post(f"{base}/_api/index", params={"collection":"edges"},
                       json=payload_edges, auth=auth, timeout=5.0, headers=headers)
        except Exception:
            pass
        log_stage(get_logger("storage"), "bootstrap", "ensure_core_indexes_ok")

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
        SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "v2")
        POLICY_VERSION = os.getenv("POLICY_VERSION", "v2")
        etag = self.get_snapshot_etag() or "noetag"
        # Include schema/policy versioning in the key so cache rotations happen
        # automatically when either changes.
        return ":".join((SCHEMA_VERSION, POLICY_VERSION, etag, *parts))

    # ---------------------------- Bulk upserts ----------------------------
    def _bulk_upsert_nodes_fast(self, docs: List[Dict[str, Any]]) -> tuple[int,int]:
        """Batch UPSERT into `nodes` by (domain,id) to avoid unique-index 409s."""
        self._connect()
        if self.db is None:
            return 0, 0
        # Sanitize keys defensively (already sanitized by caller).
        _docs = []
        for d in docs:
            dd = dict(d)
            if "_key" in dd:
                dd["_key"] = self._safe_key(str(dd["_key"]))
            _docs.append(dd)
        # Perform a single AQL UPSERT per batch keyed on (domain,id).
        aql = """
        FOR d IN @docs
          LET existed = LENGTH(FOR x IN nodes FILTER x.domain == d.domain AND x.id == d.id LIMIT 1 RETURN 1) > 0
          UPSERT { domain: d.domain, id: d.id }
            INSERT d
            UPDATE UNSET(d, ["_id","_rev"])
          IN nodes OPTIONS { keepNull: false }
          RETURN { created: !existed }
        """
        cursor = self.db.aql.execute(aql, bind_vars={"docs": _docs})
        stats = list(cursor)
        created = sum(1 for r in stats if r.get("created"))
        updated = len(_docs) - created
        return int(created), int(updated)

    def _bulk_upsert_edges_fast(self, docs: List[Dict[str, Any]]) -> tuple[int,int]:
        """Best-effort bulk replace into `edges` collection."""
        self._connect()
        if self.db is None:
            return (0, 0)
        _docs = []
        for d in docs:
            dd = dict(d)
            if "_key" in dd:
                dd["_key"] = self._safe_key(str(dd["_key"]))
            _docs.append(dd)
        try:
            coll = self.db.collection("edges")
            if hasattr(coll, "import_bulk"):
                res = coll.import_bulk(_docs, on_duplicate="replace")
                return int(res.get("created", 0)), int(res.get("updated", 0))
        except Exception as exc:
            raise
        n = 0
        for d in _docs:
            try:
                self.db.collection("edges").insert(d, overwrite=True)
                n += 1
            except Exception:
                pass
        return n, 0

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
        # Ensure a live connection (ArangoStore may have been created with lazy=True)
        self._connect()
        if self.db is None or not hasattr(self.db, "aql"):
            return 0, 0
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
            self._connect()
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
        # Enrich only canonical decisions
        if not n or n.get("type") != "DECISION":
            return None
        from core_utils.domain import storage_key_to_anchor
        anchor_id = None
        _k = n.get("_key")
        if isinstance(_k, str) and _k:
            try:
                anchor_id = storage_key_to_anchor(_k)
            except Exception:
                anchor_id = None
        if not anchor_id:
            dom = n.get("domain")
            nid = n.get("id")
            if dom and nid:
                anchor_id = f"{dom}#{nid}"
        out: Dict[str, Any] = {
            "id": anchor_id,
            "type": "DECISION",
            "title": n.get("title"),
            "description": n.get("description"),
            "timestamp": n.get("timestamp"),
            "decision_maker": n.get("decision_maker"),
            "domain": n.get("domain"),
        }

        # Merge any existing x-extra and other non-canonical keys
        extra: Dict[str, Any] = {}
        if isinstance(n.get("x-extra"), dict):
            extra.update(n.get("x-extra") or {})
        _exclude = {
            "_key","_id","_rev","id","x-extra","snapshot_etag","meta","type",
            "title","description","timestamp","decision_maker","supported_by","based_on","domain",
        }
        for k, v in n.items():
            if k in _exclude:
                continue
            extra[k] = v
        if extra:
            out["x-extra"] = extra
            try:
                log_stage(logger, "enrich", "x_extra_preserved", node_type="decision", node_id=node_id, extra_count=len(extra))
            except Exception:
                pass
        return out

    def get_enriched_event(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self.get_node(node_id)
        # Enrich only canonical events
        if not n or n.get("type") != "EVENT":
            return None
        from core_utils.domain import storage_key_to_anchor
        anchor_id = None
        _k = n.get("_key")
        if isinstance(_k, str) and _k:
            try:
                anchor_id = storage_key_to_anchor(_k)
            except Exception:
                anchor_id = None
        if not anchor_id:
            dom = n.get("domain")
            nid = n.get("id")
            if dom and nid:
                anchor_id = f"{dom}#{nid}"
        out: Dict[str, Any] = {
            "id": anchor_id,
            "type": "EVENT",
            "title": n.get("title"),
            "description": n.get("description"),
            "timestamp": n.get("timestamp"),
            "domain": n.get("domain"),
        }

        extra: Dict[str, Any] = {}
        if isinstance(n.get("x-extra"), dict):
            extra.update(n.get("x-extra") or {})
        _exclude = {
            "_key","_id","_rev","id","x-extra","snapshot_etag","meta","type",
            "title","description","timestamp","led_to","domain",
        }
        for k, v in n.items():
            if k in _exclude:
                continue
            extra[k] = v
        if extra:
            out["x-extra"] = extra
            try:
                log_stage(logger, "enrich", "x_extra_preserved", node_type="event", node_id=node_id, extra_count=len(extra))
            except Exception:
                pass
        return out

    def get_enriched_node(self, storage_key: str) -> Optional[Dict[str, Any]]:
        """
        Generic enrich by storage key: route to specific enricher by node.type.
        """
        n = self.get_node(storage_key)
        if not n or "type" not in n:
            return None
        t = (n.get("type") or "").upper()
        if t == "DECISION":
            return self.get_enriched_decision(storage_key)
        if t == "EVENT":
            return self.get_enriched_event(storage_key)
        # Future: return the stored node as a minimal enriched doc
        return {
            "id": n.get("_key"),
            "type": t or "UNKNOWN",
            "domain": n.get("domain"),
            "title": n.get("title"),
            "description": n.get("description"),
            "timestamp": n.get("timestamp"),
            "x-extra": n.get("x-extra") if isinstance(n.get("x-extra"), dict) else None,
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
                # Cache hit – parse the bytes via jsonx to maintain canonical
                # ordering.  This avoids mismatches between cached values and
                # downstream services which also use jsonx.
                core_metrics.counter("cache_hit_total", 1, service="memory_api")
                return jsonx.loads(v)
            # Cache miss – increment miss counter
            core_metrics.counter("cache_miss_total", 1, service="memory_api")
        except Exception:
            return None
        return None

    def _cache_set(self, key: str, value, ttl: int):
        r = self._redis()
        if not r:
            return
        try:
            # Persist cache entries using jsonx.dumps for canonical ordering.
            # This prevents fingerprint drift due to inconsistent key ordering
            # when reading cached data.
            r.setex(key, ttl, jsonx.dumps(value))
        except Exception:
            return

    # ------------------------------------------------------------
    # Graph expansion (k = 1)
    # ------------------------------------------------------------

    def expand_candidates(self, anchor_id: str, k: int = 1) -> dict:
        """
        Expand the graph one hop around the given anchor and return an edges-only
        view.  Per the v3 baseline, no legacy fields (neighbors, transitions,
        rel/direction) are emitted.  The result shape is:
            {"node_id": ..., "anchor": ..., "graph": {"edges": [...]}, "meta": {}}
        """
        with trace_span("storage.arango.expand_candidates", stage="resolver") as sp:
            sp.set_attribute("k", k)
            sp.set_attribute("anchor_id", anchor_id)
        # Ensure database connection; stub-mode returns empty edges
        if self.db is None:
            self._connect()
        if self.db is None:
            return {
                "node_id": anchor_id,
                "anchor": anchor_id,
                "graph": {"edges": []},
                "meta": {},
            }
        k = 1  # fixed
        cache_key = self._cache_key("expand", anchor_id, f"k{k}")
        cached = self._cache_get(cache_key)
        if cached and "graph" in cached:
            return cached
        # Build outbound and inbound edges with AQL; include domain only for ALIAS_OF
        graph_clause = f"GRAPH '{self._graph_name}'"
        aql = f"""
        LET anchor   = DOCUMENT('nodes', @anchor)
        LET outgoing = (
            anchor == null ? [] : (
                FOR v, e IN 1..1 OUTBOUND anchor {graph_clause}
                LET etype = e.type
                RETURN {{
                    type: etype,
                    from: anchor._key,
                    to: v._key,
                    timestamp: e.timestamp,
                    domain: (etype == 'ALIAS_OF' ? v.domain : null)
                }}
            )
        )
        LET incoming = (
            anchor == null ? [] : (
                /* one-hop inbound to the anchor; no legacy fallbacks */
                FOR v, e IN 1..1 INBOUND anchor {graph_clause}
                LET etype = e.type
                RETURN {{
                    type: etype,
                    from: v._key,
                    to: anchor._key,
                    timestamp: e.timestamp,
                    domain: (etype == 'ALIAS_OF' ? v.domain : null)
                }}
            )
        )
        RETURN {{ anchor: anchor, edges: UNIQUE(APPEND(outgoing, incoming)) }}
        """
        cursor = self.db.aql.execute(aql, bind_vars={"anchor": anchor_id})
        docs   = self._cursor_to_list(cursor)
        doc    = docs[0] if docs else {"anchor": None, "edges": []}
        anchor_doc = doc.get("anchor")
        if isinstance(anchor_doc, dict):
            anchor_id = anchor_doc.get("_key") or anchor_id
        edges_raw = doc.get("edges") or []
        # Deduplicate edges by (type, from, to, timestamp, domain)
        seen = set()
        edges = []
        for e in edges_raw:
            t  = (e.get("type") or "").upper()
            fr = e.get("from")
            to = e.get("to")
            ts = e.get("timestamp")
            dm = e.get("domain")
            key = (t, fr, to, ts, dm)
            if key in seen:
                continue
            seen.add(key)
            edge = {"type": t, "from": fr, "to": to, "timestamp": ts}
            if dm is not None:
                edge["domain"] = dm
            edges.append(edge)
        result = {
            "node_id": anchor_id,
            "anchor": anchor_doc,
            "graph": {"edges": edges},
            "meta": {},
        }
        self._cache_set(cache_key, result, get_settings().cache_ttl_expand_sec)
        return result

    # ------------------------------------------------------------
    # Alias-tail helper (next decisions from an event)
    # ------------------------------------------------------------
    def next_decisions_from_event(self, event_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Return up to `limit` DECISION nodes one hop OUTBOUND from the given EVENT
        over {LED_TO, CAUSAL}, scoped to the event’s domain. Ordered by
        edge.timestamp DESC, decision.timestamp DESC, decision.id ASC.
        Orientation is not computed or surfaced here.
        """
        if self.db is None:
            self._connect()
        if self.db is None:
            return []
        try:
            lim = max(0, int(limit or 0))
        except Exception:
            lim = 3
        graph_clause = f"GRAPH '{self._graph_name}'"
        aql = f"""
        LET ev = DOCUMENT('nodes', @event_id)
        FOR v, e IN 1..1 OUTBOUND ev {graph_clause}
          FILTER e.type IN ['LED_TO','CAUSAL']
          FILTER v.type == 'DECISION' && v.domain == ev.domain
          SORT e.timestamp DESC, v.timestamp DESC, v._key ASC
          LIMIT @limit
          RETURN {{
            id: v._key,
            title: v.title,
            domain: v.domain,
            timestamp: v.timestamp,
            edge: {{ type: e.type, timestamp: e.timestamp }}
          }}
        """
        cursor = self.db.aql.execute(aql, bind_vars={"event_id": event_id, "limit": lim})
        rows = self._cursor_to_list(cursor)
        return rows

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
        with trace_span("storage.arango.resolve_text", stage="resolver") as sp:
            try:
                sp.set_attribute("limit", limit)
                sp.set_attribute("use_vector", use_vector)
            except Exception:
                pass
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
        _fp = hashlib.sha1(f"{q}|{int(bool(use_vector))}".encode()).hexdigest()[:12]
        key = self._cache_key("resolve", f"h{_fp}", f"l{limit}")
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
                    with trace_span("storage.arango.aql.vector", stage="resolver") as sp:
                        try:
                            # Record query attributes.  Include limit, metric and embedding dimensionality
                            sp.set_attribute("limit", limit)
                            sp.set_attribute("metric", "cosine")
                            if query_vector is not None:
                                sp.set_attribute("vector_dim", len(query_vector))
                        except Exception:
                            pass
                        cursor = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                        results = self._cursor_to_list(cursor)
                        # Attach the number of results to the span for trace introspection
                        try:
                            sp.set_attribute("result_count", len(results))
                        except Exception:
                            pass
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
                    with trace_span("storage.arango.aql.vector", stage="resolver") as sp:
                        try:
                            # Record query attributes.  Include limit, metric and embedding dimensionality
                            sp.set_attribute("limit", limit)
                            sp.set_attribute("metric", "cosine")
                            if query_vector is not None:
                                sp.set_attribute("vector_dim", len(query_vector))
                        except Exception:
                            pass
                        cursor = self.db.aql.execute(aql, bind_vars={"qv": query_vector, "limit": limit})
                        results = self._cursor_to_list(cursor)
                        # Attach the number of results to the span for trace introspection
                        try:
                            sp.set_attribute("result_count", len(results))
                        except Exception:
                            pass
                    if hasattr(self.db, "aql"):
                        self.db.aql.latest_query = aql  # type: ignore[attr-defined]
                    resp = {"query": q, "matches": results, "vector_used": True}
                    self._cache_set(key, resp, get_settings().cache_ttl_resolve_sec)
                    return resp
                except Exception:
                    pass
        try:
            # The BM25 search uses the ArangoSearch view ``nodes_search`` (v3: title & description only).
            aql = (
                "FOR d IN nodes_search "
                "SEARCH ANALYZER( "
                "  TOKENS(@q,'text_en') ANY IN d.title OR "
                "  TOKENS(@q,'text_en') ANY IN d.description, 'text_en' ) "
                "SORT BM25(d) DESC LIMIT @limit "
                "RETURN {id: d._key, score: BM25(d), title: d.title, type: d.type}"
            )
            with trace_span("storage.arango.aql.bm25", stage="resolver") as sp:
                try:
                    sp.set_attribute("limit", limit)
                    sp.set_attribute("view", "nodes_search")
                except Exception:
                    pass
                cursor = self.db.aql.execute(aql, bind_vars={"q": q, "limit": limit})
                results = list(cursor)
                # Attach the number of results to the span so Grafana can jump from metrics to traces
                try:
                    sp.set_attribute("result_count", len(results))
                except Exception:
                    pass
            if not results:
                try:
                    terms = [t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3]
                except Exception:
                    terms = []
                if terms:
                    fields = ["title","description"]
                    ors = " OR ".join([f"LIKE(LOWER(d.{f}), LOWER(CONCAT('%', @t, '%')))" for f in fields])
                    aql_like = ("FOR t IN @terms FOR d IN nodes FILTER " + ors +
                                " COLLECT d = d WITH COUNT INTO _c LIMIT @limit "
                                " RETURN {id: d._key, score: 0.0, title: d.title, type: d.type}")
                    with trace_span("storage.arango.aql.like_fallback", stage="resolver") as sp:
                        try:
                            sp.set_attribute("limit", limit)
                            sp.set_attribute("terms", len(terms))
                        except Exception:
                            pass
                        cursor = self.db.aql.execute(aql_like, bind_vars={"terms": terms, "limit": limit})
                        results = list(cursor)
                        try:
                            sp.set_attribute("result_count", len(results))
                        except Exception:
                            pass
                    try:
                        from core_logging import log_stage, get_logger
                        log_stage(get_logger("memory_api"), "resolver", "bm25_zero_hits_like_fallback", q=q, terms=len(terms))
                    except Exception:
                        pass
        except Exception:
            # Fallback lexical search when ArangoSearch view fails (view missing or unsupported).
            # v3: search only `title` and `description`.
            aql = (
                "FOR d IN nodes "
                "FILTER LIKE(LOWER(d.title), LOWER(CONCAT('%', @q, '%'))) "
                "   OR LIKE(LOWER(d.description), LOWER(CONCAT('%', @q, '%'))) "
                "LIMIT @limit RETURN {id: d._key, score: 0.0, title: d.title, type: d.type}"
            )
            with trace_span("storage.arango.aql.lexical_fallback", stage="resolver") as sp:
                try:
                    sp.set_attribute("limit", limit)
                except Exception:
                    pass
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

    def resolve_alias_home(self, node_id: str, max_hops: int = 1) -> Optional[str]:
        """
        Follow ALIAS_OF edges up to `max_hops` to resolve to a DECISION in its home domain.
        Returns the DECISION node_id if found; otherwise returns the original node_id.
        Does not modify sensitivity; used only at read time.
        """
        if self.db is None:
            self._connect()
        if self.db is None:
            return node_id
        # Public invariant: exactly one hop. Allow callers to request fewer, never more.
        try:
            hops = 1 if int(max_hops or 1) >= 1 else 0
        except Exception:
            hops = 1
        if hops <= 0:
            return node_id
        graph_clause = f"GRAPH '{self._graph_name}'"
        aql = f"""
        LET anchor = DOCUMENT('nodes', @anchor)
        LET start  = (anchor == null ? null : anchor)
        LET path = (start == null ? [] : (
          FOR v, e, p IN 1..@hops OUTBOUND start {graph_clause}
            FILTER e.type == 'ALIAS_OF'
            PRUNE LENGTH(p.edges) >= @hops
            FILTER v.type == 'DECISION'
            LIMIT 1
            RETURN v
        ))
        RETURN LENGTH(path) > 0 ? path[0]._key : @anchor
        """
        cursor = self.db.aql.execute(aql, bind_vars={"anchor": node_id, "hops": hops})
        res = self._cursor_to_list(cursor)
        return (res[0] if res else node_id)
    
    # ------------------------------------------------------------
    # Edges-only adjacent view (k=1) — OPTIONAL new read
    # ------------------------------------------------------------
    def get_edges_adjacent(self, anchor_id: str) -> dict:
        """
        Return all edges touching `anchor_id` (INBOUND + OUTBOUND), as stored:
        {type, from, to, timestamp, domain?}. No orientation or alias tails here.
        """
        if self.db is None:
            self._connect()
        if self.db is None:
            # Stub-mode
            return {"anchor": anchor_id, "edges": [], "meta": {"snapshot_etag": ""}}

        graph_clause = f"GRAPH '{self._graph_name}'"
        aql = f"""
        LET anchor = DOCUMENT('nodes', @anchor)
        LET out_e = (
          anchor == null ? [] : (
            /* one‑hop outbound traversal */
            FOR v, e IN 1..1 OUTBOUND anchor {graph_clause}
              LET etype = e.type
              RETURN {{
                type: etype,
                from: anchor._key,
                to: v._key,
                timestamp: e.timestamp,
                domain: (etype == 'ALIAS_OF' ? e.domain : null)
              }}
          )
        )
        LET in_e = (
          anchor == null ? [] : (
            /* one‑hop inbound traversal */
            FOR v, e IN 1..1 INBOUND anchor {graph_clause}
              LET etype = e.type
              RETURN {{
                type: etype,
                from: v._key,
                to: anchor._key,
                timestamp: e.timestamp,
                domain: (etype == 'ALIAS_OF' ? e.domain : null)
              }}
          )
        )
        RETURN {{
          anchor: anchor ? anchor._key : @anchor,
          edges: UNIQUE(APPEND(out_e, in_e))
        }}
        """
        cursor = self.db.aql.execute(aql, bind_vars={"anchor": anchor_id})
        docs = self._cursor_to_list(cursor)
        view = docs[0] if docs else {"anchor": anchor_id, "edges": []}
        try:
            view.setdefault("meta", {})["snapshot_etag"] = self.get_snapshot_etag() or ""
        except Exception:
            pass
        return view
