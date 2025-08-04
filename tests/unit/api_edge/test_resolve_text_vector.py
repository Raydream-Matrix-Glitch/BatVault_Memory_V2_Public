"""
Unit-level check that ArangoStore switches to the vector-search AQL
when ENABLE_EMBEDDINGS is true and an embed() implementation exists.
"""

import importlib, types
from types import SimpleNamespace
import pytest

# force the feature flag
import os

os.environ["ENABLE_EMBEDDINGS"] = "true"

import packages.core_storage.arangodb as arango_mod

importlib.reload(arango_mod)  # pick up env

# --- fakes -----------------------------------------------------------------


class _DummyCursor:
    def batch(self):
        return []


class _DummyAQL(SimpleNamespace):
    def execute(self, query, bind_vars=None):
        self.latest_query = query
        return _DummyCursor()


class _DummyDB(SimpleNamespace):
    aql: _DummyAQL


# ---------------------------------------------------------------------------


def test_vector_aql_selected(monkeypatch):
    # stub embed() – 768-d constant vector
    monkeypatch.setattr(arango_mod, "embed", lambda _: [0.1] * 768, raising=False)

    dummy_db = _DummyDB(aql=_DummyAQL())           # supply a full fake client
    store     = arango_mod.ArangoStore(client=dummy_db)

    store.resolve_text("hello world")

    q = store.db.aql.latest_query
    assert "COSINE_SIMILARITY" in q, "lexical BM25 path was used, not vector"
