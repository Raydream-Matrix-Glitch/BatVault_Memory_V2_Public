"""
Smoke-test that ArangoDB hosts a 768-d **HNSW** vector index on `nodes.embedding`.
Skipped automatically when ARANGO_HOST is not configured.
"""

import os, pytest, requests

ARANGO = os.getenv("ARANGO_HOST")


@pytest.mark.skipif(ARANGO is None, reason="ArangoDB not available")
def test_hnsw_vector_index_present():
    url = f"http://{ARANGO}:8529/_db/_system/_api/index?collection=nodes"
    r = requests.get(url, auth=("root", os.getenv("ARANGO_ROOT_PASSWORD", "")))
    r.raise_for_status()
    indexes = r.json()
    vec = [
        i for i in indexes
        if i.get("type") == "vector" and i["name"] == "nodes_embedding_hnsw"
    ]
    assert vec, "HNSW vector index missing"
    idx = vec[0]
    assert idx["params"]["dimension"] == 768
    assert idx["params"]["indexType"] == "hnsw"