"""
Smoke-test that the bootstrap container has created the vector index
inside ArangoDB.  Skips automatically if ARANGO_HOST is not defined,
allowing local unit runs without Docker.
"""

import os, pytest
from arango import ArangoClient


@pytest.mark.skipif(
    os.getenv("ARANGO_HOST") is None, reason="ArangoDB not available"
)
def test_vector_index_present():
    host = os.getenv("ARANGO_HOST", "http://arangodb:8529")

    client = ArangoClient(hosts=host)
    sys_db = client.db("_system", username="root", password="root")
    db = sys_db.database("batvault")

    col = db.collection("decisions")
    idxs = [
        i
        for i in col.indexes()
        if i["type"] == "vector" and i["fields"] == ["embedding"]
    ]

    assert idxs, "vector index on decisions.embedding missing"
    assert idxs[0]["dimension"] == 768
    assert idxs[0]["metric"] == "cosine"
