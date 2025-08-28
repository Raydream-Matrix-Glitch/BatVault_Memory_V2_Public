from ingest.pipeline.normalize import normalize_decision


def test_tag_slugify_normalization():
    raw = {
        "id": "foo-decision",
        "option": "Foo",
        "rationale": "Because.",
        "timestamp": "2024-01-02T03:04:05Z",
        "tags": ["Strategic Pivot", "strategic_pivot", "Strategic-Pivot  "],
    }
    out = normalize_decision(raw)
    assert out["tags"] == ["strategic_pivot"]