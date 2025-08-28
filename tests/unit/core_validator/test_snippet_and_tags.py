import pytest, pydantic, core_models as cm

def test_invalid_snippet_too_long():
    bad = "x"*121
    with pytest.raises(pydantic.ValidationError):
        cm.EventModel(id="ev-x", summary="s", snippet=bad, timestamp="2024-01-01T00:00:00Z")

def test_tag_slugified():
    ev = cm.EventModel(id="ev-1", summary="s", tags=["New-Tag"], timestamp="2024-01-01T00:00:00Z")
    assert ev.tags == ["new_tag"]