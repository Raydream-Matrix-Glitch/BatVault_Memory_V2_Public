from typing import Any, Dict, List, Optional, Tuple
import os
import httpx
from arango import ArangoClient
from pydantic import BaseModel
from core_config import get_settings
from core_logging import get_logger, log_stage

logger = get_logger("memory_api")

class ArangoStore:
    def __init__(self,
                 url: str,
                 root_user: str,
                 root_password: str,
                 db_name: str,
                 graph_name: str = "batvault_graph",
                 catalog_col: str = "catalog",
                 meta_col: str = "meta"):
        self.client = ArangoClient(hosts=url)
        self.sys = self.client.db("_system", username=root_user, password=root_password)
        if not self.sys.has_database(db_name):
            self.sys.create_database(db_name)
        self.db = self.client.db(db_name, username=root_user, password=root_password)

        self.graph_name = graph_name
        self.catalog_col = catalog_col
        self.meta_col = meta_col

        # Ensure collections
        for c in ("nodes", "edges", catalog_col, meta_col):
            if not self.db.has_collection(c):
                if c == "edges":
                    self.db.create_collection(c, edge=True)
                else:
                    self.db.create_collection(c)

        # Ensure graph
        if not self.db.has_graph(graph_name):
            self.db.create_graph(graph_name)
        self.graph = self.db.graph(graph_name)
        # Edge definition: edges between nodes (single super-edge collection)
        if not self.graph.has_edge_definition("edges"):
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

    def upsert_edge(self, edge_id: str, from_id: str, to_id: str, rel_type: str, payload: Dict[str, Any]) -> None:
        doc = dict(payload)
        doc["_key"] = edge_id
        doc["_from"] = f"nodes/{from_id}"
        doc["_to"] = f"nodes/{to_id}"
        doc["type"] = rel_type
        col = self.db.collection("edges")
        col.insert(doc, overwrite=True)

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
        doc = self.db.collection(self.meta_col).get("snapshot")
        return doc.get("etag") if doc else None

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
