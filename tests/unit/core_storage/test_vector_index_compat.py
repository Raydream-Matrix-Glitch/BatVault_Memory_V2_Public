import os
import types
import httpx
from packages.core_storage.src.core_storage.arangodb import ArangoStore


def test_vector_index_retries_with_ivf_on_nlists(monkeypatch):
    """
    When Arango responds with a 400 complaining about 'nLists', the index
    creation should retry with an IVF payload.
    """
    # Enable vector index creation
    monkeypatch.setenv("ARANGO_VECTOR_INDEX_ENABLED", "true")
    monkeypatch.setenv("ARANGO_VECTOR_INDEX_TYPE", "hnsw")

    # Minimal store with fake DB name and a zero vector count
    st = ArangoStore("http://arangodb:8529", "root", "pw", "db", "g", "catalog", "meta", lazy=True)
    st.db = types.SimpleNamespace(name="testdb")
    monkeypatch.setattr(ArangoStore, "_count_vectors", lambda self: 0)

    calls = []

    class DummyResp:
        def __init__(self, status, text, headers=None):
            self.status_code = status
            self._text = text
            self._headers = headers or {"content-type": "application/json"}
        @property
        def text(self): return self._text
        def json(self): return {"errorNum": 10}
        @property
        def headers(self): return self._headers

    def fake_post(url, params=None, json=None, auth=None, timeout=None):
        calls.append(json)
        # First call fails with 'nLists' hint → expect IVF retry
        if len(calls) == 1:
            return DummyResp(400, '{"error":true,"errorMessage":"Missing required attribute \'nLists\'","errorNum":10}')
        return DummyResp(201, "")

    monkeypatch.setattr(httpx, "post", fake_post, raising=True)

    st._maybe_create_vector_index()

    assert len(calls) >= 2
    assert calls[0]["params"]["indexType"] == "hnsw"
    assert calls[1]["params"]["indexType"] == "ivf"
    assert "nLists" in calls[1]["params"]