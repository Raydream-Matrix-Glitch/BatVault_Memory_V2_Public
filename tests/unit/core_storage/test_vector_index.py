import httpx
from core_storage.arangodb import ArangoStorage

class _DummyAQL:
    def execute(self, _query):
        # No vectors present
        return iter([0])

class _DummyDB:
    name = "test"
    aql = _DummyAQL()

def test_maybe_create_vector_index_no_name_error(monkeypatch):
    """
    Regression: the method should not raise NameError due to leaked locals.
    Simulate "index already exists" to exercise the success path without Arango.
    """
    class _Resp:
        status_code = 409
        headers = {"content-type": "application/json"}
        def json(self):
            return {"errorNum": 1210}
        @property
        def text(self):
            return ""

    monkeypatch.setenv("ARANGO_VECTOR_INDEX_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    monkeypatch.setenv("VECTOR_METRIC", "cosine")

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _Resp())

    store = ArangoStorage(client=_DummyDB(), lazy=True)
    # Should not raise
    store._maybe_create_vector_index()