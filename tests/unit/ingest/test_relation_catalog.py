from ingest.catalog.field_catalog import build_relation_catalog

def test_basic_relation_catalog():
    rels = build_relation_catalog()
    assert {"LED_TO", "CAUSAL_PRECEDES"} <= set(rels)
