from memory_api.app import field_catalog, relation_catalog

def test_catalog_endpoints_smoke(monkeypatch):
    class DummyStore:
        def get_snapshot_etag(self): return "etag"
        def get_field_catalog(self): return {"id":["id"],"option":["option"]}
        def get_relation_catalog(self): return ["LED_TO"]
    import memory_api.app as app
    app.store = lambda: DummyStore()
    assert "fields" in field_catalog(type("R", (), {"headers":{}})())
    assert "relations" in relation_catalog(type("R", (), {"headers":{}})())
