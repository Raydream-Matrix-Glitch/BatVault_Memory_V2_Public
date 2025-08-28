"""
Unit-level check that `ArangoStore` switches to the **vector-search** AQL
whenever the `ENABLE_EMBEDDINGS` feature-flag is on **and** an `embed()`
implementation is available.
"""

import importlib
import os
from types import SimpleNamespace

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Test configuration
# ─────────────────────────────────────────────────────────────────────────────
# The store decides between BM25 vs. vector search at *import* time based on
# environment flags, so we must set the flag *first*.
os.environ.setdefault("ENABLE_EMBEDDINGS", "true")

import core_storage.arangodb as arango_mod  # noqa: E402
from core_storage.arangodb import ArangoStore  # noqa: E402

# Re-import so the module re-reads the updated env-vars.
importlib.reload(arango_mod)  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────


class _DummyCursor:
    """
    Minimal stub that behaves like `python-arango`’s `Cursor`.

    • Iterable →  supports `for row in cursor` and `list(cursor)`
    • Exposes `.batch()` to stay future-proof if production code
      switches to `cursor.batch()`.
    """

    def __init__(self, payload=None):
        self._payload = payload or []

    # -- iterator protocol ----------------------------------------------------
    def __iter__(self):
        return iter(self._payload)

    def __next__(self):  # pragma: no cover
        return next(iter(self._payload))

    # -- compatibility helper ------------------------------------------------
    def batch(self):
        return self._payload


class _DummyAQL(SimpleNamespace):
    """
    Captures the last AQL query so the assertion can verify
    that vector search was chosen.
    """

    def execute(self, query, bind_vars=None):
        self.latest_query = query
        # One fake hit is enough for the store logic
        return _DummyCursor([{"_key": "dummy"}])


class _DummyDB(SimpleNamespace):
    aql: _DummyAQL


# ─────────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────────


def test_vector_aql_selected(monkeypatch):
    """
    The store must generate a COSINE_SIMILARITY-based AQL when embeddings
    are enabled and an `embed()` function exists.
    """
    # Stub `embed()` – constant 768-d vector suffices for the test
    monkeypatch.setattr(arango_mod, "embed", lambda _: [0.1] * 768, raising=False)

    dummy_db = _DummyDB(aql=_DummyAQL())  # supply a fully faked client
    store = ArangoStore(client=dummy_db)

    store.resolve_text("hello world")

    generated_query = store.db.aql.latest_query
    assert (
        "COSINE_SIMILARITY" in generated_query
    ), "Lexical BM25 path was used instead of vector search"
