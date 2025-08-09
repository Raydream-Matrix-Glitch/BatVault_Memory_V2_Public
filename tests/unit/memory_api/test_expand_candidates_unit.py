import types
import pytest
from memory_api.app import store

class DummyCursor:
    def __init__(self, items): self._it = iter(items)
    def __iter__(self): return self
    def __next__(self): return next(self._it)

def test_expand_happy_path(monkeypatch):
    st = store()
    class DummyDB:
        class AQL:
            @staticmethod
            def execute(aql, bind_vars=None):
                doc = {
                    "node_id": {"_key": "A1", "type": "decision", "title": "Anchor"},
                    "neighbors": [
                        {"node":{"_key":"N1","type":"event","title":"E1"}, "edge":{"relation":"preceded_by","timestamp":"2011"}},
                        {"node":{"_key":"N2","type":"event","title":"E2"}, "edge":{"relation":"succeeded_by","timestamp":"2013"}}
                    ]
                }
                return DummyCursor([doc])
        aql = AQL()
    st.db = DummyDB()
    res = st.expand_candidates("A1", k=1)
    assert res["node_id"]["_key"] == "A1"
    assert len(res["neighbors"]) == 2
    assert {"id","type","title","edge"} <= set(res["neighbors"][0].keys())

def test_expand_missing_node_id(monkeypatch):
    st = store()
    class DummyDB:
        class AQL:
            @staticmethod
            def execute(aql, bind_vars=None):
                return DummyCursor([{"node_id": None, "neighbors": []}])
        aql = AQL()
    st.db = DummyDB()
    res = st.expand_candidates("does-not-exist", k=1)
    assert res["node_id"] is None
    assert res["neighbors"] == []
