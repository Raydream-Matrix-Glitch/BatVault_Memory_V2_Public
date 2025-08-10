from core_storage.arangodb import ArangoStore


def test_get_node_returns_none_when_stub(monkeypatch):
    """
    When the underlying ArangoDB connection is unavailable (stub‑mode),
    ``get_node`` should return ``None`` instead of raising an exception.

    This test simulates stub‑mode by forcing ``db`` to ``None`` on an
    ``ArangoStore`` instance.  Without the guard added in Milestone 4,
    accessing ``self.db.collection`` would raise an ``AttributeError``.  The
    patched implementation returns ``None``, allowing callers higher up
    the stack (e.g. FastAPI handlers) to handle missing data gracefully
    (HTTP 404 rather than HTTP 500).
    """
    # Construct the store lazily; it does not attempt to connect on init
    store = ArangoStore(url="http://example.invalid:8529", root_user="root", root_password="pass", db_name="test", lazy=True)
    # Force stub‑mode by nullifying the db attribute
    store.db = None
    # Call get_node on an arbitrary key; should not raise and should return None
    assert store.get_node("nonexistent") is None