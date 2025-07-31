"""
Ensures that build_field_catalog() promotes previously unseen canonical
fields into the alias map (self-learning behaviour).
"""

from ingest.catalog.field_catalog import build_field_catalog


def test_alias_self_learning():
    # Simulate a decision object with two JSON fields: 'foobar' and 'FooBar'
    decisions = {
        "dummy-decision": {
            "foobar": "some value",
            "FooBar": "other value",
        }
    }
    # No events or transitions in this scenario
    catalog = build_field_catalog(decisions, events={}, transitions={})

    # The canonical lowercase key 'foobar' must appear,
    # with both observed aliases preserved
    assert "foobar" in catalog, "new canonical key should be surfaced"
    assert set(catalog["foobar"]) == {"foobar", "FooBar"}, "all synonyms kept"
