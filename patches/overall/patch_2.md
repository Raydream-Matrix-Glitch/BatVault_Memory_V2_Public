A-1 Fix the SHA-256 fingerprint bug
packages/core_utils/src/core_utils/fingerprints.py

diff
Copy
Edit
--- a/packages/core_utils/src/core_utils/fingerprints.py
+++ b/packages/core_utils/src/core_utils/fingerprints.py
@@
     canon_bytes = canonical_json(envelope)
-    return hashlib.sha256(canon_bytes).hexdigest()
+    # Prepend the algorithm so the fingerprint is self-describing, e.g.
+    # "sha256:ab34…".  This matches the `"sha256:<hash>"` contract used
+    # throughout the spec and logs.
+    return "sha256:" + hashlib.sha256(canon_bytes).hexdigest()
A-2 Allow “_” in IDs everywhere they are validated
services/ingest/src/ingest/cli.py (only file that still disallowed “_”)

diff
Copy
Edit
--- a/services/ingest/src/ingest/cli.py
+++ b/services/ingest/src/ingest/cli.py
@@
-ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,}[a-z0-9]$")
+# Accept lowercase letters, digits, dash **or underscore** (spec §K & §J1).
+ID_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{2,}[a-z0-9]$")
(Both normalize.py and memory_api/app.py already used the correct pattern, so no change is required there.)

A-3 Remove the duplicate setdefault block (flake-8 B006)
services/memory_api/src/memory_api/app.py

diff
Copy
Edit
--- a/services/memory_api/src/memory_api/app.py
+++ b/services/memory_api/src/memory_api/app.py
@@
-    # ---- Milestone-2 contract guarantees -------------------------------- #
-    doc.setdefault("matches", [])
-    doc.setdefault("vector_used", bool(use_vector))
-    doc.setdefault("meta", {})
+    # ---- Milestone-2 contract guarantees -------------------------------- #
+    # (duplicate initialisation removed – keys are already set above)






B

B-1 derive_links.py — enforce based_on ↔ transitions reciprocity
packages/link_utils/src/link_utils/derive_links.py (new file)

diff
Copy
Edit
diff --git a/packages/link_utils/src/link_utils/derive_links.py b/packages/link_utils/src/link_utils/derive_links.py
new file mode 100644
--- /dev/null
+++ b/packages/link_utils/src/link_utils/derive_links.py
@@
+"""
+Utility for fixing and validating link relationships between documents.
+
+For every *parent*‐style link listed in `based_on`, we must add the
+reciprocal *child* link in the *parent*’s `transitions`, and vice-versa,
+as mandated by core-spec §P #4.
+"""
+
+from __future__ import annotations
+
+from typing import Dict, List
+
+
+def derive_links(documents: List[Dict]) -> None:
+    """
+    Mutates *documents* in-place so that the following invariant holds:
+
+    *  if `doc_a.id` appears in `doc_b["based_on"]`, then
+       `doc_b.id` appears in `doc_a["transitions"]`
+    *  the converse for `transitions` ➜ `based_on`
+
+    Missing targets are ignored (they may belong to a later ingest batch).
+    """
+
+    by_id: Dict[str, Dict] = {d["id"]: d for d in documents}
+
+    # based_on  ➜  transitions
+    for doc in documents:
+        for parent_id in doc.get("based_on", []):
+            parent = by_id.get(parent_id)
+            if not parent:
+                continue
+            transitions = parent.setdefault("transitions", [])
+            if doc["id"] not in transitions:
+                transitions.append(doc["id"])
+
+    # transitions  ➜  based_on
+    for doc in documents:
+        for child_id in doc.get("transitions", []):
+            child = by_id.get(child_id)
+            if not child:
+                continue
+            parents = child.setdefault("based_on", [])
+            if doc["id"] not in parents:
+                parents.append(doc["id"])
Add unit tests later (recommended path: services/ingest/tests/test_derive_links.py).

B-2 Schema extensions + enrichment passthrough
1 / 2 Extend the public Event V2 schema
specs/json/event.schema.json

diff
Copy
Edit
--- a/specs/json/event.schema.json
+++ b/specs/json/event.schema.json
@@
   "properties": {
@@
+    "snippet": {
+      "type": "string",
+      "description": "Short human-readable preview of the document."
+    },
+    "x-extra": {
+      "type": "object",
+      "description": "Open-ended extension bucket.",
+      "additionalProperties": true
+    },
@@
   },
   "additionalProperties": false
 }
2 / 2 Pass the new fields through the Memory-API enrich flow
services/memory_api/src/memory_api/app.py

diff
Copy
Edit
--- a/services/memory_api/src/memory_api/app.py
+++ b/services/memory_api/src/memory_api/app.py
@@
-ALLOWED_KEYS = (
-    "id", "title", "tags", "created_at",
-    "based_on", "transitions",
-    "vector_used", "matches", "meta"
-)
+ALLOWED_KEYS = (
+    "id", "title", "tags", "created_at",
+    "based_on", "transitions",
+    "vector_used", "matches", "meta",
+    # ---- New in Milestone-1 ------------------------------------------- #
+    "snippet", "x-extra"
+)
(No other code changes are needed; doc = {k: v for k, v in raw.items() if k in ALLOWED_KEYS} already copies any newly whitelisted keys.)

B-3 Bootstrap script for the Arango vector index
ops/bootstrap_arango_vector.py (new file, executable)

diff
Copy
Edit
diff --git a/ops/bootstrap_arango_vector.py b/ops/bootstrap_arango_vector.py
new file mode 755
--- /dev/null
+++ b/ops/bootstrap_arango_vector.py
@@
+#!/usr/bin/env python3
+"""
+Idempotently creates a 768-dimension HNSW vector index (cosine distance)
+on collection `embeddings` in the BatVault ArangoDB cluster.
+
+Usage:
+    ARANGO_PASSWORD=secret ./ops/bootstrap_arango_vector.py
+"""
+
+import os
+import sys
+from arango import ArangoClient
+
+DIMENSIONS = 768
+COLLECTION = "embeddings"
+INDEX_NAME = "vec_hnsw_768"
+
+
+def main() -> None:
+    client = ArangoClient(hosts=os.getenv("ARANGO_HOSTS", "http://localhost:8529"))
+    db = client.db(
+        os.getenv("ARANGO_DB", "batvault"),
+        username=os.getenv("ARANGO_USER", "root"),
+        password=os.getenv("ARANGO_PASSWORD", ""),
+    )
+
+    col = db.collection(COLLECTION)
+
+    if INDEX_NAME in (idx["name"] for idx in col.indexes()):
+        print("✓ Vector index already exists — nothing to do.")
+        return
+
+    col.add_vector_index(           # Available in ArangoDB ≥ 3.11
+        fields=["vector"],
+        name=INDEX_NAME,
+        dims=DIMENSIONS,
+        distance="cosine",
+        algorithm="hnsw",
+    )
+    print(f"✓ Created {DIMENSIONS}-d HNSW vector index on '{COLLECTION}'.")
+
+
+if __name__ == "__main__":
+    try:
+        main()
+    except Exception as exc:  # pragma: no cover
+        print(f"❌ Failed: {exc}", file=sys.stderr)
+        sys.exit(1)
B-4 Static /api/schema/rels endpoint
1 / 3 Route implementation
services/memory_api/src/memory_api/routes/schema_rels.py (new file)

diff
Copy
Edit
diff --git a/services/memory_api/src/memory_api/routes/schema_rels.py b/services/memory_api/src/memory_api/routes/schema_rels.py
new file mode 100644
--- /dev/null
+++ b/services/memory_api/src/memory_api/routes/schema_rels.py
@@
+"""Serve the canonical relationship schema used by front-end graph tools."""
+
+import json
+from pathlib import Path
+
+from fastapi import APIRouter, Response
+
+router = APIRouter()
+
+_REL_PATH = Path(__file__).with_name("rels.json")
+_CACHE = json.loads(_REL_PATH.read_text("utf-8"))
+
+
+@router.get("/api/schema/rels", tags=["schema"], summary="Relationship schema")
+async def get_rels() -> Response:
+    """Return the cached JSON relationship schema."""
+    return Response(content=json.dumps(_CACHE), media_type="application/json")
2 / 3 Bundle the static JSON
services/memory_api/src/memory_api/routes/rels.json (new file, abridged here)

Tip: Copy the object exactly from the spec’s appendix K; here is a minimal stub you can expand.

json
Copy
Edit
{
  "relationship_types": [
    "based_on",
    "transitions",
    "duplicates",
    "supersedes"
  ]
}
3 / 3 Register the router
services/memory_api/src/memory_api/app.py

diff
Copy
Edit
--- a/services/memory_api/src/memory_api/app.py
+++ b/services/memory_api/src/memory_api/app.py
@@
-from fastapi import FastAPI
+from fastapi import FastAPI
+
+from memory_api.routes import schema_rels
@@
 app = FastAPI(title="BatVault Memory-API")
+
+app.include_router(schema_rels.router)