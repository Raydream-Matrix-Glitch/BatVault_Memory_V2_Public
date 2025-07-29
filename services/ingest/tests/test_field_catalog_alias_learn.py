"""
Ensures that build_field_catalog() promotes previously unseen canonical
fields into the alias map (self-learning behaviour).
"""

from ingest.catalog.field_catalog import build_field_catalog


def test_alias_self_learning():
    observed = {"foobar": {"foobar", "FooBar"}}
    catalog = build_field_catalog(observed)

    assert "foobar" in catalog, "new canonical key should be surfaced"
    assert set(catalog["foobar"]) == {"foobar", "FooBar"}, "all synonyms kept"
